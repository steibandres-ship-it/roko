from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PlaylistConfig, PublicPlaylistType, extract_track_id, track_uri
from ..playlists import load_playlists
from ..storage import PlaylistRegistry
from .models import Artist, ExternalIdentifier, Genre, IngestionBatch, IntelligenceScore, MetricSnapshot, Track, TrackArtist, TrackGenre
from .providers.base import ProviderStatus


PUBLIC_RANKER_VERSION = "public-ranker-v2.0"
MIN_SCORE_CONFIDENCE = 0.35
MIN_SCORE_COVERAGE = 0.60
MAX_EVIDENCE_AGE = timedelta(hours=72)
ALLOWED_RIGHTS_BASIS = {
    "Soundcharts": "licensed",
    "Chartmetric": "licensed",
    "Last.fm": "provider_terms",
}
PROVIDER_ID_ALIASES = {
    "Soundcharts": ("soundcharts",),
    "Chartmetric": ("chartmetric",),
    "Last.fm": ("last-fm", "lastfm"),
}

# Stage allocation recipes and turnover bounds come from the WORLD MUSIC OS
# public-playlist brief. Recipes are targets, not forced quotas when stage data is absent.
STAGE_MIXES: dict[PublicPlaylistType, dict[str, float]] = {
    "ELITE": {},
    "NOW": {"core": 0.40, "scaling": 0.20, "breakout": 0.20, "emerging": 0.15, "seed": 0.05},
    "RISING": {"elite": 0.10, "scaling": 0.20, "breakout": 0.35, "emerging": 0.30, "seed": 0.05},
    "BREAKOUT": {},
    "DISCOVERY": {"scaling": 0.05, "breakout": 0.20, "emerging": 0.45, "seed": 0.30},
    "NEXT": {"breakout": 0.10, "emerging": 0.45, "seed": 0.45},
    "NEW_MUSIC": {},
}
RANKER_WEIGHTS: dict[PublicPlaylistType, dict[str, float]] = {
    "ELITE": {"playlist_fit": 0.20, "elite": 0.47, "freshness": 0.15, "market_affinity": 0.13, "authenticity": 0.05},
    "NOW": {"playlist_fit": 0.20, "trend": 0.42, "elite": 0.15, "freshness": 0.10, "market_affinity": 0.08, "authenticity": 0.05},
    "RISING": {"playlist_fit": 0.20, "trend": 0.25, "breakout": 0.32, "freshness": 0.05, "market_affinity": 0.13, "authenticity": 0.05},
    "BREAKOUT": {"playlist_fit": 0.20, "breakout": 0.52, "trend": 0.15, "market_affinity": 0.08, "authenticity": 0.05},
    "DISCOVERY": {"playlist_fit": 0.30, "next": 0.25, "breakout": 0.20, "freshness": 0.10, "market_affinity": 0.10, "authenticity": 0.05},
    "NEXT": {"playlist_fit": 0.30, "next": 0.43, "freshness": 0.15, "market_affinity": 0.07, "authenticity": 0.05},
    "NEW_MUSIC": {"playlist_fit": 0.25, "freshness": 0.40, "trend": 0.20, "market_affinity": 0.10, "authenticity": 0.05},
}
SCORE_TYPES_BY_PLAYLIST: dict[PublicPlaylistType, tuple[str, ...]] = {
    "ELITE": ("elite", "authenticity"),
    "NOW": ("trend", "elite", "authenticity"),
    "RISING": ("trend", "breakout", "authenticity"),
    "BREAKOUT": ("breakout", "trend", "authenticity"),
    "DISCOVERY": ("next", "breakout", "authenticity"),
    "NEXT": ("next", "authenticity"),
    "NEW_MUSIC": ("trend", "authenticity"),
}
RETRIEVERS_BY_PLAYLIST: dict[PublicPlaylistType, tuple[str, ...]] = {
    "ELITE": ("EliteRetriever",),
    "NOW": ("TrendRetriever", "EliteRetriever"),
    "RISING": ("TrendRetriever", "BreakoutRetriever"),
    "BREAKOUT": ("BreakoutRetriever", "TrendRetriever"),
    "DISCOVERY": ("NextRetriever", "BreakoutRetriever", "GenreRetriever", "MarketRetriever"),
    "NEXT": ("NextRetriever", "NewReleaseRetriever", "MarketRetriever"),
    "NEW_MUSIC": ("NewReleaseRetriever", "TrendRetriever"),
}


def _provider_statuses_by_name(statuses: list[ProviderStatus]) -> dict[str, ProviderStatus]:
    return {status.provider_name: status for status in statuses}


def _current_track_specs(playlist: PlaylistConfig) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for spec in playlist.tracks:
        value = spec.spotify_uri or spec.spotify_url
        if not value:
            continue
        try:
            result[track_uri(extract_track_id(value))] = {
                "priority": spec.priority,
                "locked": spec.locked,
                "human_profiled": spec.neuromental is not None,
            }
        except ValueError:
            continue
    return result


def _vector_fit(session: Session, track_id: str, playlist: PlaylistConfig) -> tuple[float | None, float | None]:
    dna = playlist.world_music
    if dna is None or not (dna.genre_vector or dna.subgenre_vector):
        return None, None
    memberships = session.execute(
        select(Genre.slug, TrackGenre.weight, TrackGenre.data_confidence)
        .join(TrackGenre, TrackGenre.genre_id == Genre.id)
        .where(TrackGenre.track_id == track_id)
    ).all()
    target = {**dna.genre_vector, **dna.subgenre_vector}
    fit_total = 0.0
    confidence_weight = 0.0
    for slug, track_weight, confidence in memberships:
        profile_weight = target.get(slug)
        if profile_weight is None:
            continue
        fit_total += float(track_weight) * float(profile_weight) * float(confidence)
        confidence_weight += float(track_weight) * float(profile_weight)
    if confidence_weight <= 0:
        return None, None
    fit = max(0.0, min(100.0, fit_total * 100))
    fit_confidence = max(0.0, min(1.0, fit_total / confidence_weight))
    return round(fit, 2), round(fit_confidence, 4)


def _catalog_track_for_score(
    session: Session,
    provider: str,
    provider_entity_id: str,
    canonical_entity_id: str | None,
) -> Track | None:
    aliases = PROVIDER_ID_ALIASES.get(provider, ())
    if not aliases:
        return None
    identifiers = [provider_entity_id]
    if provider in ALLOWED_RIGHTS_BASIS:
        source_url = session.scalar(
            select(MetricSnapshot.source_url)
            .where(
                MetricSnapshot.provider_name == provider,
                MetricSnapshot.provider_entity_id == provider_entity_id,
                MetricSnapshot.source_url.is_not(None),
            )
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(1)
        )
        if source_url:
            identifiers.append(source_url)
    exact_track_id = session.scalar(
        select(ExternalIdentifier.track_id)
        .where(
            ExternalIdentifier.entity_type == "track",
            ExternalIdentifier.provider.in_(aliases),
            ExternalIdentifier.identifier.in_(identifiers),
        )
        .limit(1)
    )
    if not exact_track_id or (canonical_entity_id and canonical_entity_id != exact_track_id):
        return None
    return session.get(Track, exact_track_id)


def _spotify_uri_for_track(session: Session, track_id: str) -> str | None:
    identifier = session.scalar(
        select(ExternalIdentifier.identifier)
        .where(
            ExternalIdentifier.entity_type == "track",
            ExternalIdentifier.track_id == track_id,
            ExternalIdentifier.provider == "spotify",
        )
        .limit(1)
    )
    if not identifier:
        return None
    try:
        return track_uri(extract_track_id(identifier))
    except ValueError:
        return None


def _catalog_confidence(session: Session, track: Track) -> float:
    batch = session.get(IngestionBatch, track.source_batch_id)
    return float(batch.data_confidence) if batch is not None else 0.0


def _freshness_score(
    session: Session,
    track: Track,
    as_of: date,
    playlist: PlaylistConfig,
) -> tuple[float | None, float]:
    if track.release_date is None:
        return None, 0.0
    age_days = (as_of - track.release_date).days
    if age_days < 0:
        return None, 0.0
    profile = playlist.world_music.freshness_profile if playlist.world_music else {}
    if profile:
        bucket = "0_7_days" if age_days <= 7 else "8_30_days" if age_days <= 30 else "31_90_days" if age_days <= 90 else None
        if bucket is None:
            return 0.0, _catalog_confidence(session, track)
        target_share = float(profile.get(bucket, 0.0))
        max_share = max((float(value) for value in profile.values()), default=0.0)
        if max_share <= 0:
            return None, 0.0
        return round(target_share / max_share * 100, 2), _catalog_confidence(session, track)
    # Keep a conservative 90-day fallback for playlists without a freshness recipe.
    return round(max(0.0, 1 - age_days / 90) * 100, 2), _catalog_confidence(session, track)


def _confidence_adjusted_score(value: float, confidence: float) -> float:
    """Shrink uncertain 0-100 features toward neutral instead of letting noise rank high."""
    confidence = min(1.0, max(0.0, float(confidence)))
    return round(50.0 + (float(value) - 50.0) * confidence, 2)


def _score_evidence_is_usable(
    session: Session,
    row: IntelligenceScore,
    statuses: dict[str, ProviderStatus],
) -> tuple[bool, str | None]:
    provider = row.evidence.get("source_provider") if isinstance(row.evidence, dict) else None
    if provider not in ALLOWED_RIGHTS_BASIS:
        return False, "score_source_not_supported_for_public_playlist_curation"
    status = statuses.get(provider)
    if status is None or status.state not in {"CONNECTED", "DEGRADED"}:
        return False, "score_provider_not_connected_and_verified"
    rights = ALLOWED_RIGHTS_BASIS[provider]
    source_observation = session.scalar(
        select(MetricSnapshot.captured_at)
        .where(
            MetricSnapshot.provider_name == provider,
            MetricSnapshot.provider_entity_id == row.provider_entity_id,
            MetricSnapshot.entity_type == "track",
            MetricSnapshot.market_code == row.market_code,
            MetricSnapshot.rights_basis == rights,
        )
        .order_by(MetricSnapshot.captured_at.desc())
        .limit(1)
    )
    if source_observation is None:
        return False, "authorized_source_observation_not_found"
    captured_at = source_observation.replace(tzinfo=timezone.utc) if source_observation.tzinfo is None else source_observation.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    score_at = row.calculated_at.replace(tzinfo=timezone.utc) if row.calculated_at.tzinfo is None else row.calculated_at.astimezone(timezone.utc)
    if now - captured_at > MAX_EVIDENCE_AGE or now - score_at > MAX_EVIDENCE_AGE:
        return False, "source_evidence_or_score_is_stale"
    if captured_at > now + timedelta(minutes=5) or score_at > now + timedelta(minutes=5):
        return False, "source_evidence_or_score_has_future_timestamp"
    if row.score_value is None or row.status not in {"READY", "PARTIAL"}:
        return False, "score_not_ready_for_public_playlist_ranking"
    if row.confidence < MIN_SCORE_CONFIDENCE or row.evidence_coverage < MIN_SCORE_COVERAGE:
        return False, "score_confidence_or_coverage_below_engine_gate"
    return True, None


def _latest_scores(session: Session) -> list[IntelligenceScore]:
    rows = session.scalars(
        select(IntelligenceScore)
        .where(IntelligenceScore.entity_type == "track")
        .order_by(IntelligenceScore.calculated_at.desc())
        .limit(10_000)
    ).all()
    latest: dict[tuple[str, str, str, str], IntelligenceScore] = {}
    for row in rows:
        provider = row.evidence.get("source_provider", "") if isinstance(row.evidence, dict) else ""
        key = (provider, row.score_type, row.provider_entity_id, row.market_code)
        latest.setdefault(key, row)
    return list(latest.values())


def _rank_candidate(
    playlist: PlaylistConfig,
    track: Track,
    spotify_uri: str,
    market_code: str,
    scores: dict[str, IntelligenceScore],
    fit_score: float,
    fit_confidence: float,
    session: Session,
    as_of: date,
) -> dict[str, Any] | None:
    dna = playlist.world_music
    if dna is None:
        return None
    components: dict[str, float | None] = {"playlist_fit": fit_score}
    component_confidence: dict[str, float] = {"playlist_fit": fit_confidence}
    market_weight = dna.market_vector.get(market_code)
    if market_weight is not None:
        max_market_weight = max((float(weight) for weight in dna.market_vector.values()), default=0.0)
        components["market_affinity"] = round(min(1.0, float(market_weight) / max_market_weight) * 100, 2) if max_market_weight else None
        component_confidence["market_affinity"] = 1.0
    for score_type, row in scores.items():
        if score_type in {"trend", "breakout", "elite", "next", "authenticity"} and row.score_value is not None:
            components[score_type] = float(row.score_value)
            component_confidence[score_type] = float(row.confidence)
    freshness, freshness_confidence = _freshness_score(session, track, as_of, playlist)
    if freshness is not None and freshness_confidence >= MIN_SCORE_CONFIDENCE:
        components["freshness"] = freshness
        component_confidence["freshness"] = freshness_confidence

    weights = RANKER_WEIGHTS[dna.playlist_type]
    present = {
        key: (_confidence_adjusted_score(float(components[key]), component_confidence.get(key, 0.0)), weight)
        for key, weight in weights.items()
        if components.get(key) is not None
    }
    coverage = sum(weight for _, weight in present.values())
    if coverage < MIN_SCORE_COVERAGE:
        return None
    public_score = sum(float(value) * weight for value, weight in present.values()) / coverage
    confidence = sum(component_confidence.get(key, 0.0) * weight for key, (_, weight) in present.items()) / coverage
    if confidence < MIN_SCORE_CONFIDENCE:
        return None

    primary_artist = session.execute(
        select(Artist.id, Artist.name)
        .join(TrackArtist, TrackArtist.artist_id == Artist.id)
        .where(TrackArtist.track_id == track.id)
        .order_by(TrackArtist.position, Artist.id)
        .limit(1)
    ).first()

    score_sources = sorted({
        str(row.evidence.get("source_provider"))
        for row in scores.values()
        if isinstance(row.evidence, dict) and row.evidence.get("source_provider")
    })
    complete = coverage >= 1.0 - 1e-9
    contributions = {
        key: {
            "raw_score": round(float(components[key]), 2),
            "confidence_adjusted_score": value,
            "weight": round(weight, 4),
            "weighted_contribution": round(value * weight / coverage, 2),
            "confidence": round(component_confidence.get(key, 0.0), 4),
        }
        for key, (value, weight) in present.items()
    }
    return {
        "track_id": track.id,
        "spotify_uri": spotify_uri,
        "title": track.title,
        "artist": primary_artist.name if primary_artist else None,
        "artist_id": primary_artist.id if primary_artist else None,
        "market_code": market_code,
        "playlist_type": dna.playlist_type,
        "proposed_priority": "growth" if dna.playlist_type in {"NOW", "RISING", "ELITE"} else "discovery",
        "public_playlist_score": round(public_score, 2),
        "ranker_state": "COMPLETE" if complete else "PARTIAL_COMPONENT_COVERAGE",
        "ranker_complete": complete,
        "score_confidence": round(confidence, 4),
        "evidence_coverage": round(coverage, 4),
        "components": {key: value for key, (value, _) in present.items()},
        "raw_components": {key: round(float(value), 2) for key, value in components.items() if key in weights and value is not None},
        "component_contributions": contributions,
        "unavailable_dimensions": [key for key, weight in weights.items() if components.get(key) is None],
        "sources": score_sources,
        "algorithm_version": PUBLIC_RANKER_VERSION,
        "state": "CANDIDATE",
        "playlist_ready": False,
        "review_required": True,
        "artist_stage": "DATA_NOT_AVAILABLE",
        "position_state": "WAITING_FOR_PLAYLIST_HISTORY",
        "max_position_jump": dna.max_position_jump,
        "sequence_state": "WAITING_FOR_HUMAN_TRACK_CONTEXT",
    }


def _market_slot_quotas(market_vector: dict[str, float], slots: int) -> dict[str, int]:
    weights = {market: max(0.0, float(weight)) for market, weight in market_vector.items() if float(weight) > 0}
    total = sum(weights.values())
    if slots <= 0 or total <= 0:
        return {}
    exact = {market: slots * weight / total for market, weight in weights.items()}
    quotas = {market: math.floor(value) for market, value in exact.items()}
    remainder = slots - sum(quotas.values())
    remainder_order = sorted(exact, key=lambda market: (-(exact[market] - quotas[market]), market))
    for market in remainder_order[:remainder]:
        quotas[market] += 1
    return quotas


def _select_diverse_portfolio(
    ranked: list[dict[str, Any]],
    playlist: PlaylistConfig,
    selection_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Meet rounded market-mix targets first, then refill from rank order without forcing quotas."""
    dna = playlist.world_music
    market_vector = dna.market_vector if dna else {}
    quotas = _market_slot_quotas(market_vector, selection_limit)
    artist_limit = dna.max_same_artist if dna else 2
    selected: list[dict[str, Any]] = []
    artist_counts: dict[str, int] = defaultdict(int)
    seen_tracks: set[str] = set()
    selected_by_market: dict[str, int] = defaultdict(int)

    def add_candidate(candidate: dict[str, Any], allocation: str) -> bool:
        track_uri = str(candidate.get("spotify_uri") or "")
        artist_id = str(candidate.get("artist_id") or "")
        if not track_uri or track_uri in seen_tracks or (artist_id and artist_counts[artist_id] >= artist_limit):
            return False
        selected.append({**candidate, "portfolio_allocation": allocation})
        seen_tracks.add(track_uri)
        if artist_id:
            artist_counts[artist_id] += 1
        selected_by_market[str(candidate.get("market_code") or "UNKNOWN")] += 1
        return True

    market_order = sorted(quotas, key=lambda market: (-float(market_vector.get(market, 0.0)), market))
    for market in market_order:
        for _ in range(quotas[market]):
            candidate = next(
                (
                    item for item in ranked
                    if item.get("market_code") == market
                    and item.get("spotify_uri") not in seen_tracks
                    and (not item.get("artist_id") or artist_counts[str(item["artist_id"])] < artist_limit)
                ),
                None,
            )
            if candidate is None:
                break
            add_candidate(candidate, "MARKET_TARGET")

    for candidate in ranked:
        if len(selected) >= selection_limit:
            break
        add_candidate(candidate, "RANKED_FLEX_FILL")

    target_met = bool(quotas) and all(selected_by_market.get(market, 0) >= quota for market, quota in quotas.items())
    diagnostics = {
        "policy": "LARGEST_REMAINDER_MARKET_TARGETS_THEN_RANKED_FLEX_FILL",
        "market_slot_targets": quotas,
        "selected_by_market": dict(sorted(selected_by_market.items())),
        "market_targets_met": target_met if quotas else None,
        "artist_cap": artist_limit,
        "selection_limit": selection_limit,
        "selected_count": len(selected),
        "unfilled_slots": max(0, selection_limit - len(selected)),
    }
    return selected, diagnostics


def _profile_payload(config: PlaylistConfig) -> dict[str, Any]:
    dna = config.world_music
    if dna is None:
        return {}
    return dna.model_dump(mode="json")


def _build_one_draft(
    session: Session,
    config: PlaylistConfig,
    scores: list[IntelligenceScore],
    statuses: dict[str, ProviderStatus],
    *,
    limit: int,
    initial_seed: bool = False,
) -> dict[str, Any]:
    dna = config.world_music
    expected_scores = set(SCORE_TYPES_BY_PLAYLIST[dna.playlist_type]) if dna else set()
    reasons: dict[str, int] = defaultdict(int)
    groups: dict[str, dict[str, Any]] = {}
    target_markets = set(config.markets)
    existing_uris = _current_track_specs(config)

    for row in scores:
        if row.score_type not in expected_scores:
            continue
        if row.market_code not in target_markets and not (not target_markets and row.market_code == "GLOBAL"):
            continue
        usable, reason = _score_evidence_is_usable(session, row, statuses)
        if not usable:
            reasons[reason or "score_evidence_unavailable"] += 1
            continue
        provider = str(row.evidence.get("source_provider"))
        track = _catalog_track_for_score(session, provider, row.provider_entity_id, row.canonical_entity_id)
        if track is None:
            reasons["canonical_track_identity_not_resolved"] += 1
            continue
        spotify_uri = _spotify_uri_for_track(session, track.id)
        if spotify_uri is None:
            reasons["spotify_catalog_identity_not_linked"] += 1
            continue
        fit_score, fit_confidence = _vector_fit(session, track.id, config)
        if fit_score is None or fit_confidence is None:
            reasons["playlist_genre_fit_not_resolved"] += 1
            continue
        key = f"{track.id}:{row.market_code}:{provider}"
        group = groups.setdefault(key, {
            "track": track,
            "spotify_uri": spotify_uri,
            "market_code": row.market_code,
            "fit_score": fit_score,
            "fit_confidence": fit_confidence,
            "scores": {},
            "source_provider": provider,
        })
        existing = group["scores"].get(row.score_type)
        if existing is None or float(row.score_value or 0) > float(existing.score_value or 0):
            group["scores"][row.score_type] = row

    ranked = []
    for group in groups.values():
        item = _rank_candidate(
            config,
            group["track"],
            group["spotify_uri"],
            group["market_code"],
            group["scores"],
            group["fit_score"],
            group["fit_confidence"],
            session,
            datetime.now(timezone.utc).date(),
        )
        if item is None:
            reasons["public_ranker_evidence_below_gate"] += 1
            continue
        if not item["artist_id"]:
            reasons["canonical_primary_artist_not_resolved"] += 1
            continue
        if item["spotify_uri"] in existing_uris:
            reasons["already_in_playlist"] += 1
            continue
        ranked.append(item)

    ranked.sort(key=lambda item: (-item["public_playlist_score"], -item["score_confidence"], item["title"].casefold(), item["track_id"]))
    turnover_budget = math.floor(config.target_tracks * (dna.turnover_max if dna else 0.0))
    # A new playlist needs a full initial selection. The turnover limit only
    # governs later changes to an existing playlist.
    selection_limit = min(limit, config.target_tracks) if initial_seed else min(limit, turnover_budget)
    selected, portfolio_composition = _select_diverse_portfolio(ranked, config, selection_limit)

    if selected:
        state = "REVIEW_ONLY"
    elif not any(status.state in {"CONNECTED", "DEGRADED"} for status in statuses.values()):
        state = "DATA_NOT_AVAILABLE"
        reasons["no_authorized_metrics_provider_connected"] += 1
    elif not scores:
        state = "DATA_NOT_AVAILABLE"
        reasons["no_source_backed_playlist_scores"] += 1
    else:
        state = "NO_ELIGIBLE_CANDIDATES"
    mix = dna.artist_stage_profile if dna else {}
    stage_data_available = False
    return {
        "slug": config.slug,
        "name": config.name,
        "spotify_registered": bool(PlaylistRegistry().get(config.slug)),
        "spotify_url": (PlaylistRegistry().get(config.slug) or {}).get("spotify_url"),
        "playlist_type": dna.playlist_type if dna else "UNCONFIGURED",
        "status": state,
        "target_tracks": config.target_tracks,
        "configured_tracks": len(config.tracks),
        "current_track_count": len(_current_track_specs(config)),
        "existing_priority_mix": {
            priority: sum(1 for track in config.tracks if track.priority == priority)
            for priority in ("anchor", "growth", "discovery")
        },
        "markets": config.markets,
        "rotation": config.rotation.model_dump(mode="json"),
        "playlist_dna": _profile_payload(config),
        "stage_mix_target": mix,
        "stage_mix_applied": False,
        "stage_mix_state": "DATA_NOT_AVAILABLE" if mix and not stage_data_available else "NOT_CONFIGURED",
        "retrievers": list(RETRIEVERS_BY_PLAYLIST[dna.playlist_type]) if dna else [],
        "lifecycle_state": "ACTIVE" if PlaylistRegistry().get(config.slug) else "PLANNED",
        "lifecycle_signals_state": "DATA_NOT_AVAILABLE",
        "authority_score": None,
        "health_scores": {name: {"value": None, "state": "DATA_NOT_AVAILABLE"} for name in (
            "quality", "authority", "freshness", "trend", "discovery", "breakout",
            "elite", "diversity", "sequence", "authenticity", "saturation",
        )},
        "turnover_budget_max": math.floor(config.target_tracks * (dna.turnover_max if dna else 0.0)),
        "selection_phase": "INITIAL_SEED" if initial_seed else "ROTATION",
        "eligible_candidate_count": len(ranked),
        "candidate_count": len(selected),
        "portfolio_composition": portfolio_composition,
        "candidates": selected,
        "blockers": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items())],
        "publish_state": "NOT_PUBLISHED",
        "spotify_writes_enabled": False,
    }


def generate_public_playlist_seed_drafts(
    session: Session,
    provider_statuses: list[ProviderStatus],
    configs: list[PlaylistConfig],
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Build initial selections with the same provenance and fit gates as rotations."""
    statuses = _provider_statuses_by_name(provider_statuses)
    scores = _latest_scores(session)
    return [
        _build_one_draft(session, config, scores, statuses, limit=limit, initial_seed=True)
        for config in configs
    ]


def generate_public_playlist_drafts(
    session: Session,
    provider_statuses: list[ProviderStatus],
    *,
    playlist_slug: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 100)
    configs = load_playlists()
    if playlist_slug:
        configs = [config for config in configs if config.slug == playlist_slug]
        if not configs:
            raise ValueError(f"Unknown playlist slug: {playlist_slug}")
    provider_status_map = _provider_statuses_by_name(provider_statuses)
    latest_scores = _latest_scores(session)
    items = [
        _build_one_draft(session, config, latest_scores, provider_status_map, limit=bounded_limit)
        for config in configs
    ]
    return {
        "network": "WORLD MUSIC NETWORK",
        "algorithm_version": PUBLIC_RANKER_VERSION,
        "draft_only": True,
        "spotify_writes_enabled": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist_count": len(items),
        "candidate_count": sum(item["candidate_count"] for item in items),
        "items": items,
        "method": {
            "source_gate": "Only verified connected providers with a recorded allowed rights basis are considered.",
            "identity_gate": "Requires exact provider-to-catalog identity and an exact Spotify track identifier.",
            "fit_gate": "Requires licensed/owned/consented track genre metadata matching playlist DNA and a target-market score.",
            "score_gate": f"Only READY/PARTIAL source-backed scores with confidence >= {MIN_SCORE_CONFIDENCE} and evidence coverage >= {MIN_SCORE_COVERAGE} are ranked; component scores are confidence-shrunk toward neutral 50.",
            "algorithm_version": PUBLIC_RANKER_VERSION,
            "portfolio_composition": "Rounded largest-remainder market slots are filled first; missing market slots are flex-filled by rank. Artist caps and canonical-track deduplication remain hard constraints.",
            "freshness_gate": f"Scores and authorized evidence older than {int(MAX_EVIDENCE_AGE.total_seconds() // 3600)} hours are excluded.",
            "stage_mix": "Artist-stage proportions are targets; they are not applied until authorized stage data exists.",
            "turnover": "A ranked draft is capped by each playlist's configured maximum turnover; existing tracks remain untouched.",
            "sequence": "Candidate additions do not reorder the current playlist. Energy/tempo/mood transitions remain unavailable until authorized track-level metadata exists.",
        },
    }


def public_playlist_registry_view(provider_statuses: list[ProviderStatus]) -> dict[str, Any]:
    registry = PlaylistRegistry().all()
    configs = load_playlists()
    return {
        "playlist_count": len(configs),
        "registered_count": sum(bool(registry.get(config.slug, {}).get("spotify_playlist_id")) for config in configs),
        "data_state": "DATA_NOT_AVAILABLE" if not any(status.state in {"CONNECTED", "DEGRADED"} for status in provider_statuses) else "WAITING_FOR_PLAYLIST_SCORES",
        "items": [
            {
                "slug": config.slug,
                "name": config.name,
                "public": config.public,
                "playlist_type": config.world_music.playlist_type if config.world_music else "UNCONFIGURED",
                "retrievers": list(RETRIEVERS_BY_PLAYLIST[config.world_music.playlist_type]) if config.world_music else [],
                "target_tracks": config.target_tracks,
                "configured_tracks": len(config.tracks),
                "current_track_count": len(_current_track_specs(config)),
                "markets": config.markets,
                "rotation": config.rotation.model_dump(mode="json"),
                "playlist_dna": _profile_payload(config),
                "lifecycle_state": "ACTIVE" if registry.get(config.slug) else "PLANNED",
                "lifecycle_signals_state": "DATA_NOT_AVAILABLE",
                "authority_score": None,
                "health_scores": {name: {"value": None, "state": "DATA_NOT_AVAILABLE"} for name in (
                    "quality", "authority", "freshness", "trend", "discovery", "breakout",
                    "elite", "diversity", "sequence", "authenticity", "saturation",
                )},
                "priority_mix": {
                    priority: sum(1 for track in config.tracks if track.priority == priority)
                    for priority in ("anchor", "growth", "discovery")
                },
                "registered": bool(registry.get(config.slug, {}).get("spotify_playlist_id")),
                "spotify_url": registry.get(config.slug, {}).get("spotify_url"),
            }
            for config in configs
        ],
        "registry_state": "EXISTING_PLAYLISTS_REGISTERED" if configs and registry else "PLAYLISTS_NOT_REGISTERED",
        "spotify_writes_enabled": False,
    }
