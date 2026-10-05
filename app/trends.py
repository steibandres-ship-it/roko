from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from rich.console import Console

from .auth import OAuthManager
from .config import DATA_DIR, PLAYLISTS_DIR
from .models import PlaylistConfig, TrackSpec
from .playlists import load_playlists
from .spotify_client import SpotifyClient
from .storage import JsonStore, PlaylistRegistry, utc_now
from .sync import item_uri, sync_playlist
from .tracks import TrackResolver


console = Console()
HISTORY_PATH = DATA_DIR / "trend_rotation_history.json"
LOCAL_TZ = ZoneInfo("America/Santiago")
MAX_DAILY_CHANGES = 10
NEW_TRACK_COOLDOWN_DAYS = 7
REENTRY_COOLDOWN_DAYS = 30
MIN_CHART_RANK = 50

FIT_RULES: dict[str, tuple[set[str], set[str]]] = {
    "reggaeton-worldwide": ({"latin"}, {"urban", "reggaeton", "perreo"}),
    "latin-urban-takeover": ({"latin"}, {"urban", "reggaeton", "trap", "dembow"}),
    "latin-viral-100": ({"latin"}, set()),
    "malianteo-worldwide": ({"latin"}, {"urban", "dembow", "rap"}),
    "next-latin-stars": ({"latin", "emerging"}, set()),
    "perreo-mundial": ({"latin"}, {"perreo", "reggaeton", "dance"}),
    "rnb-latino-nights": ({"latin", "rnb"}, set()),
    "indie-rock-worldwide": (set(), {"indie", "alternative", "rock"}),
    "latin-indie-rock": ({"latin"}, {"indie", "alternative", "rock"}),
    "next-rock-generation": ({"emerging"}, {"indie", "alternative", "rock"}),
}

SOURCE_WEIGHTS = {"viral": 2.0, "genre": 1.5, "market": 1.25, "global": 1.0}


class ChartSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["Shazam", "Billboard"]
    chart: str = Field(min_length=2, max_length=100)
    kind: Literal["viral", "genre", "market", "global"]
    rank: int = Field(ge=1, le=200)
    url: str = Field(min_length=12, max_length=500)
    observed_on: date

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: str, info: Any) -> str:
        host = (urlparse(value).hostname or "").lower()
        source = info.data.get("source")
        if source == "Shazam" and not (host == "shazam.com" or host.endswith(".shazam.com")):
            raise ValueError("Shazam signals must link to shazam.com")
        if source == "Billboard" and not (host == "billboard.com" or host.endswith(".billboard.com")):
            raise ValueError("Billboard signals must link to billboard.com")
        if urlparse(value).scheme != "https":
            raise ValueError("chart source links must use HTTPS")
        return value


class TrendCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    artist: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    fit_tags: set[str] = Field(min_length=1)
    signals: list[ChartSignal] = Field(min_length=1, max_length=10)


class TrendBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    candidates: list[TrendCandidate] = Field(min_length=1, max_length=100)


def load_trend_batch(path: Path) -> TrendBatch:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        batch = TrendBatch.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Could not load a valid trend-candidate file: {exc}") from exc
    return batch


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _fit_is_valid(candidate: TrendCandidate) -> bool:
    required, one_of = FIT_RULES.get(candidate.playlist, (set(), set()))
    tags = {tag.casefold().strip() for tag in candidate.fit_tags}
    return required <= tags and (not one_of or bool(one_of & tags))


def _candidate_strength(candidate: TrendCandidate) -> float:
    signals = [signal for signal in candidate.signals if signal.rank <= MIN_CHART_RANK]
    if not signals:
        return 0.0
    points = sum(SOURCE_WEIGHTS[signal.kind] * (MIN_CHART_RANK + 1 - signal.rank) / MIN_CHART_RANK for signal in signals)
    independent = len({(signal.source, signal.chart.casefold()) for signal in signals})
    return points + min(0.5, max(0, independent - 1) * 0.15)


def _track_is_human_protected(track: dict[str, Any]) -> bool:
    if bool(track.get("locked")):
        return True
    notes = track.get("neuromental")
    if not isinstance(notes, dict):
        return False
    return any(value not in (None, [], "") for value in notes.values())


def _track_uri(track: dict[str, Any]) -> str | None:
    direct = track.get("spotify_uri") or track.get("spotify_url")
    if not direct:
        return None
    try:
        from .models import extract_track_id, track_uri

        return track_uri(extract_track_id(str(direct)))
    except ValueError:
        return None


def _added_at_by_uri(items: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        uri = item_uri(item)
        if uri:
            added = item.get("added_at")
            if added:
                result.setdefault(uri, str(added))
    return result


def _recently_added(uri: str, added_at: dict[str, str], history: list[dict[str, Any]], today: date) -> bool:
    timestamp = added_at.get(uri)
    if timestamp:
        try:
            added = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if added.tzinfo is None:
                added = added.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - added < timedelta(hours=24):
                return True
        except ValueError:
            return True
    for row in history:
        if row.get("added_uri") != uri or row.get("playlist") is None:
            continue
        try:
            added_day = date.fromisoformat(str(row.get("as_of")))
        except ValueError:
            continue
        if 0 <= (today - added_day).days < NEW_TRACK_COOLDOWN_DAYS:
            return True
    return False


def _recently_removed(uri: str, playlist: str, history: list[dict[str, Any]], today: date) -> bool:
    for row in history:
        if row.get("playlist") != playlist or row.get("removed_uri") != uri:
            continue
        try:
            removed_day = date.fromisoformat(str(row.get("as_of")))
        except ValueError:
            continue
        if 0 <= (today - removed_day).days < REENTRY_COOLDOWN_DAYS:
            return True
    return False


def _same_day_done(playlist: str, day: date, history: list[dict[str, Any]]) -> bool:
    return any(row.get("playlist") == playlist and row.get("as_of") == day.isoformat() and row.get("status") == "applied" for row in history)


def _select_eviction(
    raw_tracks: list[dict[str, Any]],
    config: PlaylistConfig,
    priority: Literal["growth", "discovery"],
    items: list[dict[str, Any]],
    history: list[dict[str, Any]],
    today: date,
    registry_record: dict[str, Any],
) -> int | None:
    added_at = _added_at_by_uri(items)
    locked_uris = set(registry_record.get("locked_uris", []))
    candidates: list[int] = []
    for index, (raw_track, spec) in enumerate(zip(raw_tracks, config.tracks, strict=True)):
        if spec.priority != priority or _track_is_human_protected(raw_track):
            continue
        uri = _track_uri(raw_track)
        if not uri or uri in locked_uris or _recently_added(uri, added_at, history, today):
            continue
        if not raw_track.get("spotify_catalog_verified"):
            continue
        candidates.append(index)
    return candidates[-1] if candidates else None


def _make_updated_playlist(raw: dict[str, Any], candidate: TrendCandidate, uri: str, priority: Literal["growth", "discovery"], evict_index: int) -> dict[str, Any]:
    updated = deepcopy(raw)
    tracks = list(updated["tracks"])
    old = tracks.pop(evict_index)
    new_track = {
        "artist": candidate.artist,
        "title": candidate.title,
        "spotify_uri": uri,
        "priority": priority,
        "locked": False,
        "spotify_catalog_verified": True,
    }
    insert_at = next((i for i, track in enumerate(tracks) if track.get("priority") == priority), len(tracks))
    tracks.insert(insert_at, new_track)
    updated["tracks"] = tracks
    return updated


def _adopt_single_spotify_addition(
    raw: dict[str, Any], config: PlaylistConfig, current_uris: list[str]
) -> tuple[dict[str, Any], PlaylistConfig] | None:
    configured_uris = [_track_uri(track) for track in raw.get("tracks", [])]
    if (
        len(set(current_uris)) != len(current_uris)
        or len(current_uris) != len(configured_uris) + 1
        or any(uri is None for uri in configured_uris)
        or not set(configured_uris).issubset(set(current_uris))
    ):
        return None
    known = {str(_track_uri(track)): deepcopy(track) for track in raw["tracks"]}
    merged: list[dict[str, Any]] = []
    for uri in current_uris:
        track = known.get(uri)
        if track is None:
            track = {
                "spotify_uri": uri,
                "priority": "discovery",
                "locked": True,
                "spotify_catalog_verified": False,
            }
        merged.append(track)
    updated = deepcopy(raw)
    updated["tracks"] = merged
    updated["target_tracks"] = len(current_uris)
    rotation = dict(updated.get("rotation") or {})
    rotation["enabled"] = True
    rotation["discovery_slots"] = int(rotation.get("discovery_slots") or 0) + 1
    updated["rotation"] = rotation
    return updated, PlaylistConfig.model_validate(updated)


def _save_playlist_config(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def rotate_from_charts(
    batch: TrendBatch,
    spotify: SpotifyClient,
    *,
    owner_id: str,
    apply: bool = False,
    max_changes: int = MAX_DAILY_CHANGES,
    registry: PlaylistRegistry | None = None,
    history_path: Path | None = None,
) -> dict[str, int]:
    if not 0 <= max_changes <= MAX_DAILY_CHANGES:
        raise ValueError(f"max_changes must be between 0 and {MAX_DAILY_CHANGES}")
    today = local_today()
    if apply and batch.as_of != today:
        raise ValueError(f"apply requires trend data dated today in America/Santiago ({today.isoformat()})")
    history_store = JsonStore(history_path or HISTORY_PATH)
    history = history_store.read([])
    registry = registry or PlaylistRegistry()
    configs = {config.slug: config for config in load_playlists()}
    raw_by_slug = {
        slug: json.loads((PLAYLISTS_DIR / f"{slug}.json").read_text(encoding="utf-8"))
        for slug in configs
    }
    proposals: dict[str, list[TrendCandidate]] = {}
    for candidate in batch.candidates:
        if candidate.playlist not in configs:
            console.print(f"Skipped unknown playlist: {candidate.playlist}")
            continue
        if not _fit_is_valid(candidate):
            console.print(f"Skipped [bold]{configs[candidate.playlist].name}[/bold]: candidate does not match its genre profile.")
            continue
        fresh_signals = [
            signal
            for signal in candidate.signals
            if signal.rank <= MIN_CHART_RANK and 0 <= (batch.as_of - signal.observed_on).days <= 7
        ]
        if not fresh_signals:
            continue
        candidate = candidate.model_copy(update={"signals": fresh_signals})
        proposals.setdefault(candidate.playlist, []).append(candidate)

    proposals = {
        slug: sorted(candidates, key=lambda candidate: (-_candidate_strength(candidate), candidate.title.casefold(), candidate.artist.casefold()))
        for slug, candidates in proposals.items()
    }
    results = {"applied": 0, "previewed": 0, "already_present": 0, "unmatched": 0, "skipped": 0}
    resolver = TrackResolver(spotify)
    applied_this_run = 0
    for slug, candidates in proposals.items():
        if applied_this_run >= max_changes:
            console.print(f"Daily cap reached ({max_changes}); remaining playlist rotations deferred.")
            break
        config = configs[slug]
        raw = raw_by_slug[slug]
        record = registry.get(slug) or {}
        playlist_id = str(record.get("spotify_playlist_id") or "")
        if not playlist_id or not config.rotation.enabled:
            console.print(f"Skipped [bold]{config.name}[/bold]: playlist is unregistered or daily rotation is disabled.")
            results["skipped"] += 1
            continue
        if _same_day_done(slug, batch.as_of, history):
            console.print(f"Skipped [bold]{config.name}[/bold]: already rotated today.")
            results["skipped"] += 1
            continue
        if len(config.tracks) != config.target_tracks or any(
            not track.spotify_uri or not (track.spotify_catalog_verified or track.locked) for track in config.tracks
        ):
            console.print(f"Skipped [bold]{config.name}[/bold]: local tracks are not fully verified for safe exact rotation.")
            results["skipped"] += 1
            continue

        try:
            details = spotify.get_playlist(playlist_id)
            playlist_owner = str((details.get("owner") or {}).get("id") or record.get("owner_id") or "")
            if playlist_owner != owner_id:
                raise ValueError("registered playlist is not owned by the connected Spotify account")
            items = spotify.get_playlist_items(playlist_id)
        except Exception as exc:
            console.print(f"Skipped [bold]{config.name}[/bold]: Spotify could not verify the playlist ({type(exc).__name__}).")
            results["skipped"] += 1
            continue

        current_uris = [uri for item in items if (uri := item_uri(item))]
        configured_uris = [_track_uri(track) for track in raw["tracks"]]
        adopted_existing_item = False
        if len(items) != config.target_tracks or current_uris != configured_uris:
            adopted = _adopt_single_spotify_addition(raw, config, current_uris)
            if adopted is None:
                console.print(f"Skipped [bold]{config.name}[/bold]: Spotify differs from the saved order; reconcile it before rotating.")
                results["skipped"] += 1
                continue
            raw, config = adopted
            adopted_existing_item = True

        selected: tuple[TrendCandidate, str, float, Literal["growth", "discovery"]] | None = None
        for candidate in candidates:
            spec = TrackSpec(artist=candidate.artist, title=candidate.title)
            resolved, _summaries, _reason = resolver.resolve(spec)
            if not resolved:
                console.print(f"No exact enough Spotify catalog match: {candidate.artist} — {candidate.title}")
                results["unmatched"] += 1
                continue
            uri = resolved.uri
            if uri in set(current_uris):
                console.print(f"Already on [bold]{config.name}[/bold]: {candidate.artist} — {candidate.title}")
                results["already_present"] += 1
                continue
            if _recently_removed(uri, slug, history, today):
                continue
            priority: Literal["growth", "discovery"] = "discovery" if slug.startswith("next-") or "emerging" in candidate.fit_tags else "growth"
            evict = _select_eviction(raw["tracks"], config, priority, items, history, today, record)
            if evict is None:
                console.print(f"No eligible {priority} slot in [bold]{config.name}[/bold]; the chart candidate was left unchanged.")
                continue
            selected = (candidate, uri, _candidate_strength(candidate), priority)
            break

        if selected is None:
            results["skipped"] += 1
            continue
        candidate, uri, strength, priority = selected
        evict_index = _select_eviction(raw["tracks"], config, priority, items, history, today, record)
        if evict_index is None:
            results["skipped"] += 1
            continue
        signals = ", ".join(f"{signal.source} {signal.chart} #{signal.rank}" for signal in candidate.signals)
        console.print(
            f"[bold]{config.name}[/bold]: {candidate.artist} — {candidate.title} | {signals} | score {strength:.2f} | replace 1 {priority} slot"
        )
        if adopted_existing_item:
            console.print(f"  Preserving one Spotify-added item as locked; playlist size stays {config.target_tracks}.")
        if not apply:
            results["previewed"] += 1
            continue

        path = PLAYLISTS_DIR / f"{slug}.json"
        updated_raw = _make_updated_playlist(raw, candidate, uri, priority, evict_index)
        updated_config = PlaylistConfig.model_validate(updated_raw)
        original_bytes = path.read_bytes()
        try:
            _save_playlist_config(path, updated_raw)
            outcome = sync_playlist(
                updated_config,
                spotify,
                registry=registry,
                mode="exact",
                yes=True,
                owner_id=owner_id,
                token_data=OAuthManager(spotify.settings).token_store.load(),
            )
            if outcome.get("blocked_unresolved") or outcome.get("cancelled"):
                raise RuntimeError("Spotify exact sync did not complete")
        except Exception as exc:
            path.write_bytes(original_bytes)
            safe_detail = getattr(exc, "strerror", None) or str(exc)
            safe_detail = safe_detail.replace("spotify:track:", "spotify:track:<")
            console.print(
                f"[red]Rotation failed for {config.name} ({type(exc).__name__}: {safe_detail}); "
                "local configuration was rolled back.[/red]"
            )
            results["skipped"] += 1
            continue

        entry = {
            "as_of": batch.as_of.isoformat(),
            "timestamp": utc_now(),
            "playlist": slug,
            "title": candidate.title,
            "artist": candidate.artist,
            "source_signals": [signal.model_dump(mode="json") for signal in candidate.signals],
            "priority": priority,
            "added_uri": uri,
            "removed_uri": _track_uri(raw["tracks"][evict_index]),
            "status": "applied",
        }
        history.append(entry)
        history_store.write(history[-1000:])
        raw_by_slug[slug] = updated_raw
        results["applied"] += 1
        applied_this_run += 1
    return results
