from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import unicodedata
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import PlaylistConfig
from ..spotify_client import SpotifyAPIError
from .models import MetricSnapshot
from .providers.lastfm import LastFmAPIError, LastFmProvider, _track_rows


MAX_REVIEW_CANDIDATES = 12
MAX_CHART_CAPTURE_AGE = timedelta(days=8)
MAX_SOUNDCHARTS_CAPTURE_AGE = timedelta(hours=72)
LASTFM_TAGS_URL = "https://www.last.fm/api/show/track.getTopTags"
TAG_ALIASES = {
    "alt-rock": "alternative-rock",
    "alt-rock-and-roll": "alternative-rock",
    "alternative": "alternative-rock",
    "alt": "alternative-rock",
    "latin-alt": "latin-alternative",
    "latin-indie": "latin-alternative",
    "r-and-b": "rnb",
    "rhythm-and-blues": "rnb",
    "hip-hop": "hip-hop",
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def _spotify_query(title: str, artist: str) -> str:
    clean_title = " ".join(title.replace('"', " ").split())
    clean_artist = " ".join(artist.replace('"', " ").split())
    return f'track:"{clean_title}" artist:"{clean_artist}"'


def _target_genres(playlist: PlaylistConfig) -> set[str]:
    dna = playlist.world_music
    if dna is None:
        return set()
    return {
        TAG_ALIASES.get(_key(tag), _key(tag))
        for tag, weight in {**dna.genre_vector, **dna.subgenre_vector}.items()
        if weight > 0
    }


def _tag_names(payload: dict[str, Any]) -> list[str]:
    tags = payload.get("toptags")
    rows = tags.get("tag") if isinstance(tags, dict) else None
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        name = row["name"].strip()[:100]
        normalized = TAG_ALIASES.get(_key(name), _key(name))
        if name and normalized and normalized not in seen:
            names.append(name)
            seen.add(normalized)
        if len(names) == 10:
            break
    return names


def _exact_spotify_tracks(items: list[dict[str, Any]], title: str, artist: str) -> list[dict[str, Any]]:
    title_key = _key(title)
    artist_key = _key(artist)
    exact: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or _key(str(item.get("name") or "")) != title_key:
            continue
        artists = item.get("artists") if isinstance(item.get("artists"), list) else []
        artist_names = [str(row.get("name") or "") for row in artists if isinstance(row, dict)]
        if not any(_key(name) == artist_key for name in artist_names):
            continue
        track_id = str(item["id"])
        if not re.fullmatch(r"[A-Za-z0-9]{1,64}", track_id):
            continue
        exact[track_id] = {
            "id": track_id,
            "name": str(item.get("name") or "")[:240],
            "artist": ", ".join(artist_names[:4])[:240],
            "url": (
                (item.get("external_urls") or {}).get("spotify")
                if isinstance(item.get("external_urls"), dict)
                else None
            ) or f"https://open.spotify.com/track/{track_id}",
        }
    return list(exact.values())


def _chart_pool(
    session: Session,
    playlist: PlaylistConfig,
) -> tuple[list[dict[str, Any]], list[str], datetime | None, datetime | None]:
    dna = playlist.world_music
    target_markets = list(dna.market_vector) if dna and dna.market_vector else list(playlist.markets)
    target_markets = list(dict.fromkeys(market.upper() for market in target_markets if market.upper() != "GLOBAL"))
    if not target_markets:
        target_markets = ["GLOBAL"]

    grouped: dict[str, dict[str, Any]] = {}
    newest: datetime | None = None
    oldest: datetime | None = None
    now = datetime.now(timezone.utc)

    def candidate_for(title: str, artist: str) -> dict[str, Any]:
        identity = f"{_key(title)}::{_key(artist)}"
        return grouped.setdefault(identity, {
            "title": title,
            "artist": artist,
            "markets": {},
            "soundcharts_markets": {},
            "lastfm_url": None,
            "soundcharts_url": None,
        })

    # The official Soundcharts catalog identifies these `global-N` Spotify
    # charts as daily Top Songs charts. Keep positions separated by market and
    # only use current (<=72 h), licensed observations.
    soundcharts_cutoff = now - MAX_SOUNDCHARTS_CAPTURE_AGE
    for market in target_markets:
        latest_soundcharts = session.scalar(
            select(func.max(MetricSnapshot.captured_at)).where(
                MetricSnapshot.provider_name == "Soundcharts",
                MetricSnapshot.rights_basis == "licensed",
                MetricSnapshot.entity_type == "track",
                MetricSnapshot.metric_code.like("chart_position_global_%"),
                MetricSnapshot.market_code == market,
            )
        )
        if latest_soundcharts is None or _utc(latest_soundcharts) < soundcharts_cutoff:
            continue
        capture = _utc(latest_soundcharts)
        if capture > now + timedelta(minutes=5):
            continue
        if newest is None or capture > newest:
            newest = capture
        if oldest is None or capture < oldest:
            oldest = capture
        rows = session.scalars(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.provider_name == "Soundcharts",
                MetricSnapshot.rights_basis == "licensed",
                MetricSnapshot.entity_type == "track",
                MetricSnapshot.metric_code.like("chart_position_global_%"),
                MetricSnapshot.market_code == market,
                MetricSnapshot.captured_at == latest_soundcharts,
            )
            .order_by(MetricSnapshot.value.asc())
            .limit(100)
        ).all()
        previous_rows = session.scalars(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.provider_name == "Soundcharts",
                MetricSnapshot.rights_basis == "licensed",
                MetricSnapshot.entity_type == "track",
                MetricSnapshot.metric_code.like("chart_position_global_%"),
                MetricSnapshot.market_code == market,
                MetricSnapshot.captured_at < latest_soundcharts,
                MetricSnapshot.captured_at >= soundcharts_cutoff,
            )
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(500)
        ).all()
        previous_by_track: dict[tuple[str, str], MetricSnapshot] = {}
        for previous_row in previous_rows:
            previous_by_track.setdefault((previous_row.provider_entity_id, previous_row.metric_code), previous_row)
        for row in rows:
            if not row.entity_label or not row.artist_label:
                continue
            candidate = candidate_for(row.entity_label, row.artist_label)
            previous = previous_by_track.get((row.provider_entity_id, row.metric_code))
            movement_places = None
            movement_period_hours = None
            if previous is not None:
                current_observed = _utc(row.observed_at)
                previous_observed = _utc(previous.observed_at)
                movement_period_hours = round((current_observed - previous_observed).total_seconds() / 3600, 1)
                if movement_period_hours < 20:
                    movement_period_hours = None
                else:
                    movement_places = round(float(previous.value) - float(row.value), 2)
            candidate["soundcharts_markets"][market] = {
                "position": row.value,
                "captured_at": _utc(row.captured_at).isoformat(),
                "previous_position": float(previous.value) if previous is not None and movement_period_hours is not None else None,
                "movement_places": movement_places,
                "movement_period_hours": movement_period_hours,
            }
            if row.source_url:
                candidate["soundcharts_url"] = row.source_url

    # Last.fm's country charts represent a weekly window. Preserve their ranks
    # as a distinct provider signal; never average them with Soundcharts ranks.
    lastfm_cutoff = now - MAX_CHART_CAPTURE_AGE
    for market in target_markets:
        latest = session.scalar(
            select(func.max(MetricSnapshot.captured_at)).where(
                MetricSnapshot.provider_name == "Last.fm",
                MetricSnapshot.rights_basis == "provider_terms",
                MetricSnapshot.metric_code == "lastfm_chart_rank",
                MetricSnapshot.market_code == market,
            )
        )
        if latest is None or _utc(latest) < lastfm_cutoff or _utc(latest) > now + timedelta(minutes=5):
            continue
        if newest is None or _utc(latest) > newest:
            newest = _utc(latest)
        if oldest is None or _utc(latest) < oldest:
            oldest = _utc(latest)
        rows = session.scalars(
            select(MetricSnapshot).where(
                MetricSnapshot.provider_name == "Last.fm",
                MetricSnapshot.rights_basis == "provider_terms",
                MetricSnapshot.metric_code == "lastfm_chart_rank",
                MetricSnapshot.market_code == market,
                MetricSnapshot.captured_at == latest,
            ).order_by(MetricSnapshot.value.asc()).limit(100)
        ).all()
        for row in rows:
            if not row.entity_label or not row.artist_label or not row.source_url:
                continue
            entry = candidate_for(row.entity_label, row.artist_label)
            entry["markets"][market] = {"position": row.value, "captured_at": _utc(row.captured_at).isoformat()}
            entry["lastfm_url"] = row.source_url

    # A transparent ordering rule, not a composite trend score: chart presence in
    # more target markets first, then source-separated Soundcharts and Last.fm ranks.
    def improved_market_count(item: dict[str, Any]) -> int:
        return sum(
            1
            for values in item["soundcharts_markets"].values()
            if values.get("movement_places") is not None and values["movement_places"] > 0
        )

    candidates = sorted(
        grouped.values(),
        key=lambda item: (
            -improved_market_count(item),
            -len(set(item["markets"]) | set(item["soundcharts_markets"])),
            -len(item["soundcharts_markets"]),
            -len(item["markets"]),
            min((values["position"] for values in item["soundcharts_markets"].values()), default=float("inf")),
            min((values["position"] for values in item["markets"].values()), default=float("inf")),
            item["title"].casefold(),
        ),
    )
    return candidates[:MAX_REVIEW_CANDIDATES], target_markets, newest, oldest


def generate_lastfm_playlist_review(
    session: Session,
    playlist: PlaylistConfig,
    lastfm: LastFmProvider,
    spotify: Any,
) -> dict[str, Any]:
    """Return short-lived Last.fm-to-Spotify review matches without persisting either API response."""
    if not lastfm.scope_confirmed:
        raise PermissionError("Last.fm non-commercial and outside-EEA use scope is not confirmed.")
    if not lastfm.credentials_configured:
        raise RuntimeError("Last.fm API key is not configured.")
    pool, target_markets, captured_at, oldest_capture = _chart_pool(session, playlist)
    if not pool:
        return {
            "playlist_slug": playlist.slug,
            "playlist_name": playlist.name,
            "status": "NO_RECENT_CHARTS",
            "captured_at": None,
            "oldest_captured_at": None,
            "candidate_count": 0,
            "candidates": [],
            "target_markets": target_markets,
            "method": "No hay capturas recientes de Soundcharts (máximo 72 h) ni de Last.fm (máximo 8 días) en los mercados objetivo.",
        }

    targets = _target_genres(playlist)
    reviews: list[dict[str, Any]] = []
    for candidate in pool:
        tag_names: list[str] = []
        tag_state = "UNAVAILABLE"
        try:
            response = lastfm.fetch_track_top_tags(candidate["artist"], candidate["title"], limit=10)
            tag_names = _tag_names(response.payload)
            tag_state = "AVAILABLE" if tag_names else "NO_TAGS_REPORTED"
        except LastFmAPIError as exc:
            tag_state = "RATE_LIMITED" if exc.code in {"29", "429", "LOCAL_REQUEST_BUDGET_REACHED"} else "UNAVAILABLE"

        match_tags = sorted({TAG_ALIASES.get(_key(tag), _key(tag)) for tag in tag_names} & targets)
        try:
            found = spotify.search_tracks(_spotify_query(candidate["title"], candidate["artist"]), limit=10)
            exact = _exact_spotify_tracks(found, candidate["title"], candidate["artist"])
        except SpotifyAPIError:
            raise
        except Exception as exc:
            raise RuntimeError("Spotify catalog search is unavailable. Check the saved authorization and try again.") from None

        spotify_match = exact[0] if len(exact) == 1 else None
        if len(exact) == 1 and match_tags:
            review_state = "REVIEW_READY"
        elif len(exact) > 1:
            review_state = "SPOTIFY_MATCH_AMBIGUOUS"
        elif len(exact) == 1:
            review_state = "GENRE_UNCONFIRMED"
        else:
            review_state = "SPOTIFY_MATCH_NOT_EXACT"

        reviews.append({
            **candidate,
            "market_coverage_count": len(set(candidate["markets"]) | set(candidate["soundcharts_markets"])),
            "soundcharts_market_coverage_count": len(candidate["soundcharts_markets"]),
            "lastfm_market_coverage_count": len(candidate["markets"]),
            "provider_join_basis": (
                "normalized_title_and_artist"
                if candidate["markets"] and candidate["soundcharts_markets"]
                else "single_provider"
            ),
            "soundcharts_improved_market_count": improved_market_count(candidate),
            "lastfm_tags": tag_names,
            "matched_playlist_genres": match_tags,
            "tag_state": tag_state,
            "spotify_match_state": review_state,
            "spotify_match": spotify_match,
            "ambiguous_spotify_match_count": max(0, len(exact) - 1),
        })

    return {
        "playlist_slug": playlist.slug,
        "playlist_name": playlist.name,
        "status": "REVIEW_ONLY",
        "captured_at": captured_at.isoformat() if captured_at else None,
        "oldest_captured_at": oldest_capture.isoformat() if oldest_capture else None,
        "candidate_count": len(reviews),
        "review_ready_count": sum(item["spotify_match_state"] == "REVIEW_READY" for item in reviews),
        "lastfm_tag_requests": lastfm.requests_made,
        "target_markets": target_markets,
        "candidates": reviews,
        "lastfm_tags_source_url": LASTFM_TAGS_URL,
        "method": "Revisión local, no score: se prioriza el número de mercados donde el puesto Soundcharts mejoró desde una captura diaria comparable; después la presencia total en mercados, puestos Soundcharts actuales y puestos Last.fm por separado. El cambio exige dos fechas observadas separadas por al menos 20 h; si no hay historial se deja vacío. Los charts diarios de Spotify reportados por Soundcharts deben tener capturas <=72 h y los charts semanales Last.fm <=8 días. Tags Last.fm sirven solo para encaje de género. Cuando ambas fuentes coinciden, la unión usa título y artista normalizados, no un ID canónico; la búsqueda exacta en Spotify puede seguir siendo ambigua. No se crea score combinado ni se modifica Spotify.",
    }
