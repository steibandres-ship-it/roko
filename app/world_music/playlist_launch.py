from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
import unicodedata

from sqlalchemy.orm import Session

from ..auth import OAuthManager, TokenStore
from ..config import DATA_DIR, PLAYLISTS_DIR, load_settings
from ..models import PlaylistConfig, PublicPlaylistDNA, RotationConfig, TrackSpec, extract_track_id
from ..playlists import playlist_url
from ..spotify_client import SpotifyClient
from ..storage import JsonStore, PlaylistRegistry, utc_now
from ..sync import item_uri
from .models import Artist, Track
from .playlist_blueprints import ordered_playlist_blueprints
from .providers.base import ProviderStatus
from .public_playlists import STAGE_MIXES, generate_public_playlist_seed_drafts


MIN_SEED_TRACKS = 30
MIN_SEED_ARTISTS = 20
MIN_MEAN_CONFIDENCE = 0.55
MAX_EXISTING_LIST_OVERLAP = 0.60
SEED_TARGET_TRACKS = 50
MAX_NEW_PLAYLISTS_PER_RUN = 10
LOCAL_ORIGIN_MARKETS = {
    "reggaeton-chile-rising": "CL",
    "reggaeton-colombia-rising": "CO",
    "reggaeton-argentina-rising": "AR",
    "reggaeton-puerto-rico-rising": "PR",
    "dembow-dominicano-rising": "DO",
    "indie-argentina-discovery": "AR",
    "indie-chile-rising": "CL",
    "afrobeats-nigeria-next": "NG",
    "funk-brasileiro-discovery": "BR",
    "techno-alemania-now": "DE",
}
SONIC_REVIEW_REQUIRED = {"rnb-latino-chill", "techno-alemania-now", "global-dancefloor-pulse"}


def _energy_vector(label: str) -> dict[str, float]:
    if label == "variada":
        return {"energy_2": 0.25, "energy_3": 0.25, "energy_4": 0.25, "energy_5": 0.25}
    level = {"baja": 2, "media": 3, "media-alta": 4, "alta": 4, "muy alta": 5}.get(label)
    return {f"energy_{level}": 1.0} if level else {}


def _sequence_review_matches(slug: str, uris: list[str]) -> bool:
    path = DATA_DIR / "sequence_reviews" / f"{slug}.json"
    try:
        review = JsonStore(path).read({})
    except (OSError, ValueError):
        return False
    return (
        isinstance(review, dict)
        and bool(str(review.get("reviewer") or "").strip())
        and review.get("approved_spotify_uris") == uris
    )


def config_from_blueprint(blueprint: dict[str, Any]) -> PlaylistConfig:
    """Turn an editorial brief into validated playlist DNA, without publishing it."""
    kind = blueprint["playlist_type"]
    freshness = {"0_7_days": 0.35, "8_30_days": 0.65} if kind == "NEW_MUSIC" else {}
    discovery_share = {"NOW": 0.30, "RISING": 0.60, "BREAKOUT": 0.55, "DISCOVERY": 0.75, "NEXT": 0.85, "NEW_MUSIC": 0.60}.get(kind, 0.5)
    return PlaylistConfig(
        slug=blueprint["slug"],
        name=blueprint["name"],
        description=blueprint["description"],
        public=True,
        target_tracks=SEED_TARGET_TRACKS,
        tracks=[],
        markets=list(blueprint["markets"]),
        rotation=RotationConfig(enabled=True, keep_anchor_tracks=10, growth_slots=20, discovery_slots=20),
        world_music=PublicPlaylistDNA(
            playlist_type=kind,
            genre_vector=dict(blueprint["genre_vector"]),
            subgenre_vector=dict(blueprint["subgenre_vector"]),
            market_vector=dict(blueprint["market_vector"]),
            energy_profile=_energy_vector(blueprint["energy"]),
            language_profile={blueprint["language"]: 1.0} if blueprint["language"] in {"es", "en", "pt", "fr", "ja", "ko", "pa"} else {},
            artist_stage_profile=STAGE_MIXES[kind],
            freshness_profile=freshness,
            mainstream_discovery_balance=discovery_share,
            turnover_min=0.10,
            turnover_max=0.20,
            refresh_interval_hours_min=24,
            refresh_interval_hours_max=168,
            max_same_artist=2,
            max_position_jump=26,
        ),
    )


def build_blueprint_launch_plans(
    session: Session,
    statuses: list[ProviderStatus],
    *,
    existing_configs: list[PlaylistConfig],
    slug: str | None = None,
) -> list[dict[str, Any]]:
    blueprints = [item for item in ordered_playlist_blueprints() if slug is None or item["slug"] == slug]
    if slug is not None and not blueprints:
        raise ValueError(f"Unknown blueprint slug: {slug}")
    configs = [config_from_blueprint(item) for item in blueprints]
    drafts = generate_public_playlist_seed_drafts(session, statuses, configs, limit=SEED_TARGET_TRACKS)
    existing_sets = {
        config.slug: {spec.spotify_uri for spec in config.tracks if spec.spotify_uri}
        for config in existing_configs
    }
    plans: list[dict[str, Any]] = []
    registry = PlaylistRegistry()
    today = datetime.now(timezone.utc).date()
    for blueprint, config, draft in zip(blueprints, configs, drafts):
        candidates = list(draft["candidates"])
        uris = [str(item["spotify_uri"]) for item in candidates]
        artists = {str(item["artist_id"]) for item in candidates if item.get("artist_id")}
        mean_confidence = round(sum(float(item["score_confidence"]) for item in candidates) / len(candidates), 4) if candidates else 0.0
        maximum_overlap = max(
            ((len(set(uris) & track_set) / len(uris), name) for name, track_set in existing_sets.items() if uris and name != config.slug),
            default=(0.0, None),
        )
        blockers: list[str] = []
        existing_record = registry.get(config.slug)
        if existing_record and existing_record.get("launch_state") != "SEEDING":
            blockers.append("already_registered")
        if any(existing.slug != config.slug and existing.name.casefold() == config.name.casefold() for existing in existing_configs):
            blockers.append("name_collides_with_existing_config")
        if len(uris) < MIN_SEED_TRACKS:
            blockers.append("minimum_tracks_not_met")
        if len(artists) < MIN_SEED_ARTISTS:
            blockers.append("minimum_artists_not_met")
        if mean_confidence < MIN_MEAN_CONFIDENCE:
            blockers.append("mean_source_confidence_below_gate")
        origin_market = blueprint.get("origin_market") or LOCAL_ORIGIN_MARKETS.get(config.slug)
        if origin_market and artists:
            local_count = 0
            for artist_id in artists:
                artist = session.get(Artist, artist_id)
                if artist is not None and artist.country_code == origin_market:
                    local_count += 1
            if local_count / len(artists) < 0.60:
                blockers.append("local_artist_origin_below_gate")
        if len(config.markets) >= 4 and candidates:
            represented = {str(item.get("market_code") or "") for item in candidates}
            if len(represented & set(config.markets)) < min(4, len(config.markets)):
                blockers.append("insufficient_market_diversity")
        if config.slug in SONIC_REVIEW_REQUIRED and candidates and not _sequence_review_matches(config.slug, uris):
            blockers.append("sonic_sequence_review_required")
        if maximum_overlap[0] > MAX_EXISTING_LIST_OVERLAP:
            blockers.append("not_distinct_from_existing_playlist")
        if config.world_music and config.world_music.playlist_type == "NEW_MUSIC":
            for candidate in candidates:
                track = session.get(Track, candidate["track_id"])
                if track is None or track.release_date is None or not 0 <= (today - track.release_date).days <= 30:
                    blockers.append("release_date_outside_30_day_window")
                    break
        plans.append({
            "slug": config.slug,
            "name": config.name,
            "portfolio_order": blueprint["portfolio_order"],
            "portfolio_phase": blueprint["portfolio_phase"],
            "portfolio_wave": blueprint["portfolio_wave"],
            "status": "READY_TO_LAUNCH" if not blockers else "BLOCKED_PENDING_EVIDENCE",
            "candidate_count": len(uris),
            "unique_artist_count": len(artists),
            "mean_score_confidence": mean_confidence,
            "highest_overlap": {"fraction": round(maximum_overlap[0], 4), "playlist": maximum_overlap[1] if maximum_overlap[0] else None},
            "highest_concept_overlap": {"fraction": 0.0, "playlist": None},
            "source_providers": sorted({source for item in candidates for source in item.get("sources", [])}),
            "blockers": blockers,
            "ranker_blockers": draft["blockers"],
            "candidates": candidates,
            "config": config,
            "campaign_objective": blueprint["campaign_objective"],
            "target_audience": blueprint["target_audience"],
            "measurement_plan": blueprint["measurement_plan"],
        })
    for plan in plans:
        own_uris = {item["spotify_uri"] for item in plan["candidates"]}
        if not own_uris:
            continue
        maximum = max(
            (
                (len(own_uris & {item["spotify_uri"] for item in other["candidates"]}) / len(own_uris), other["name"])
                for other in plans if other is not plan
            ),
            default=(0.0, None),
        )
        plan["highest_concept_overlap"] = {"fraction": round(maximum[0], 4), "playlist": maximum[1] if maximum[0] else None}
        if maximum[0] > MAX_EXISTING_LIST_OVERLAP:
            plan["blockers"].append("not_distinct_from_another_concept")
            plan["status"] = "BLOCKED_PENDING_EVIDENCE"
    return plans


def plan_public_view(plan: dict[str, Any]) -> dict[str, Any]:
    """Expose evidence and launch state without leaking internal model objects."""
    return {key: value for key, value in plan.items() if key != "config"}


def _identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _verify_spotify_identities(plan: dict[str, Any], spotify: SpotifyClient, cache: dict[str, dict[str, Any]]) -> None:
    """Use Spotify only to confirm catalog identity, never as a trend metric."""
    for candidate in plan["candidates"]:
        track_id = extract_track_id(str(candidate["spotify_uri"]))
        item = cache.get(track_id)
        if item is None:
            item = spotify.get_track(track_id)
            cache[track_id] = item
        artist_names = {_identity_key(str(artist.get("name") or "")) for artist in item.get("artists") or []}
        if (
            str(item.get("id") or "") != track_id
            or _identity_key(str(item.get("name") or "")) != _identity_key(str(candidate["title"]))
            or _identity_key(str(candidate.get("artist") or "")) not in artist_names
        ):
            raise ValueError(f"Spotify catalog identity did not match the approved candidate in {plan['slug']}")


def publish_ready_blueprint(
    plan: dict[str, Any],
    spotify: SpotifyClient,
    *,
    owner_id: str,
    known_playlists: list[dict[str, Any]],
    spotify_track_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Seed a private Spotify playlist, verify its tracks, then make it public."""
    if plan["status"] != "READY_TO_LAUNCH":
        raise ValueError(f"{plan['slug']} did not pass the playlist creation gate")
    config: PlaylistConfig = plan["config"]
    registry = PlaylistRegistry()
    record = registry.get(config.slug)
    if record and record.get("launch_state") == "ACTIVE":
        return {"slug": config.slug, "status": "ALREADY_ACTIVE", "url": record.get("spotify_url")}
    if record and record.get("owner_id") != owner_id:
        raise ValueError(f"{config.slug} is registered to a different Spotify account")
    matches = [
        item for item in known_playlists
        if str(item.get("name") or "").casefold() == config.name.casefold()
        and str((item.get("owner") or {}).get("id") or "") == owner_id
    ]
    if not record and matches:
        raise ValueError(f"An owned playlist named {config.name!r} already exists; recover its ID before launch")

    uris = [str(item["spotify_uri"]) for item in plan["candidates"]]
    if len(uris) != len(set(uris)) or len(uris) < MIN_SEED_TRACKS:
        raise ValueError("initial selection is incomplete or contains duplicate tracks")
    _verify_spotify_identities(plan, spotify, spotify_track_cache)
    config.tracks = [
        TrackSpec(spotify_uri=uri, spotify_catalog_verified=True, priority=item["proposed_priority"])
        for uri, item in zip(uris, plan["candidates"])
    ]
    playlist_id = str((record or {}).get("spotify_playlist_id") or "")
    if not playlist_id:
        created = spotify.create_playlist(config.name, config.description, False)
        playlist_id = str(created["id"])
        registry.update(config.slug, {
            "spotify_playlist_id": playlist_id,
            "spotify_url": playlist_url(created),
            "created_at": utc_now(),
            "owner_id": owner_id,
            "launch_state": "SEEDING",
            "last_synced": None,
            "locked_uris": [],
        })
        known_playlists.append(created)
    details = spotify.get_playlist(playlist_id)
    if str((details.get("owner") or {}).get("id") or "") != owner_id:
        raise ValueError(f"Spotify ownership check failed for {config.slug}")
    current = [uri for item in spotify.get_playlist_items(playlist_id) if (uri := item_uri(item))]
    if current != uris:
        spotify.replace_items(playlist_id, uris)
        current = [uri for item in spotify.get_playlist_items(playlist_id) if (uri := item_uri(item))]
    if current != uris:
        raise ValueError(f"Spotify did not confirm the complete initial selection for {config.slug}; it remains private")

    path: Path = PLAYLISTS_DIR / f"{config.slug}.json"
    # Keep the local configuration private until Spotify confirms publication.
    private_config = config.model_copy(update={"public": False})
    JsonStore(path).write(private_config.model_dump(mode="json"))
    spotify.update_playlist(playlist_id, name=config.name, description=config.description, public=True)
    published = spotify.get_playlist(playlist_id)
    if published.get("public") is not True:
        raise ValueError(f"Spotify did not confirm public visibility for {config.slug}")
    JsonStore(path).write(config.model_dump(mode="json"))
    registry.update(config.slug, {"launch_state": "ACTIVE", "last_synced": utc_now()})
    return {"slug": config.slug, "status": "PUBLISHED", "url": (registry.get(config.slug) or {}).get("spotify_url"), "tracks": len(uris)}


def publish_ready_blueprints(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = sorted(
        (plan for plan in plans if plan["status"] == "READY_TO_LAUNCH"),
        key=lambda plan: int(plan.get("portfolio_order") or 0),
    )[:MAX_NEW_PLAYLISTS_PER_RUN]
    if not ready:
        return []
    settings = load_settings()
    manager = OAuthManager(settings)
    token = TokenStore(settings.client_id).load() or {}
    scopes = set(str(token.get("scope") or "").split())
    required = {"playlist-modify-private", "playlist-modify-public"}
    if not required.issubset(scopes):
        raise ValueError("Spotify session needs playlist-modify-private and playlist-modify-public scopes")
    results: list[dict[str, Any]] = []
    with SpotifyClient(settings, manager) as spotify:
        owner_id = str(spotify.get_me().get("id") or "")
        if not owner_id:
            raise ValueError("Spotify did not return an account ID")
        known = spotify.get_playlists()
        track_cache: dict[str, dict[str, Any]] = {}
        for plan in ready:
            results.append(publish_ready_blueprint(plan, spotify, owner_id=owner_id, known_playlists=known, spotify_track_cache=track_cache))
    return results
