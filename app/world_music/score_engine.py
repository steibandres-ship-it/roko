from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from .metrics import Point, calculate_window_metrics, percentile_rank
from .models import IntelligenceScore, MetricSnapshot, now_utc
from .scoring import calculate_score


SCORED_TYPES = ("trend", "breakout", "next")
MIN_COHORT = 20


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _normalized_window_features(
    *,
    entity_id: str,
    market_code: str,
    relevant: list[tuple[tuple[str, str, str, str], object]],
    grouped: dict,
    velocity_cohorts: dict,
    acceleration_cohorts: dict,
    platform_counts: dict,
    platform_count_cohorts: dict,
    market_counts: dict,
    market_count_cohort: list[float],
    now: datetime,
) -> tuple[dict[str, float | None], int, int]:
    velocity_percentiles: list[float] = []
    acceleration_percentiles: list[float] = []
    persistence_values: list[float] = []
    freshness_values: list[float] = []
    observation_count = 0
    for key, window in relevant:
        cohort_key = (key[1], key[2], key[3])
        velocity_pct = percentile_rank(
            window.velocity_per_day,
            velocity_cohorts.get(cohort_key, []),
            higher_is_better=True,
            minimum_cohort=MIN_COHORT,
        )
        acceleration_pct = percentile_rank(
            window.acceleration_per_day2,
            acceleration_cohorts.get(cohort_key, []),
            higher_is_better=True,
            minimum_cohort=MIN_COHORT,
        )
        if velocity_pct is not None:
            velocity_percentiles.append(velocity_pct)
        if acceleration_pct is not None:
            acceleration_percentiles.append(acceleration_pct)
        if window.persistence is not None:
            persistence_values.append(window.persistence)
        latest = max(grouped[key], key=lambda item: _utc(item.observed_at))
        age = max(0.0, (now - _utc(latest.observed_at)).total_seconds() / 86400)
        freshness_values.append(max(0.0, min(1.0, 1 - age / 14)))
        observation_count += window.observations

    platforms = platform_counts.get((entity_id, market_code), set())
    platform_diversity = percentile_rank(
        float(len(platforms)),
        platform_count_cohorts.get(market_code, []),
        minimum_cohort=MIN_COHORT,
    )
    number_of_markets = market_counts.get(entity_id, 0)
    geo_expansion = percentile_rank(
        float(number_of_markets),
        market_count_cohort,
        minimum_cohort=MIN_COHORT,
    ) if number_of_markets else None
    return ({
        "velocity": mean(velocity_percentiles) if velocity_percentiles else None,
        "acceleration": mean(acceleration_percentiles) if acceleration_percentiles else None,
        "cross_platform": platform_diversity,
        "geo_expansion": geo_expansion,
        "persistence": mean(persistence_values) if persistence_values else None,
        "freshness": mean(freshness_values) if freshness_values else None,
    }, len(platforms), observation_count)


def recalculate_trend_scores(session: Session) -> dict[str, int]:
    """Calculate explainable partial Trend, Breakout, and NEXT scores.

    Only observed, licensed ranks, provider-reported weekly changes, and release dates are used. Rank improvements are not
    converted into streams or numeric growth rates. Comparative percentiles require at
    least 20 records in the same market/platform/metric cohort. Scores whose feature
    coverage is below the declared threshold are saved with a null value and an
    INSUFFICIENT state so a previous score cannot masquerade as current evidence.
    Chartmetric does not provide a comparable per-observation confidence/coverage value,
    so available results remain LOW_CONFIDENCE.
    """
    session.flush()
    rows = session.scalars(
        select(MetricSnapshot)
        .where(MetricSnapshot.provider_name == "Chartmetric")
        .order_by(MetricSnapshot.observed_at.asc())
    ).all()
    grouped: dict[tuple[str, str, str, str], list[MetricSnapshot]] = defaultdict(list)
    recent_platforms: dict[tuple[str, str], set[str]] = defaultdict(set)
    recent_markets: dict[str, set[str]] = defaultdict(set)
    entity_meta: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    release_dates: dict[str, date] = {}
    now = now_utc()
    recent_cutoff = now.timestamp() - 14 * 86400
    for row in rows:
        if row.entity_type != "track":
            continue
        grouped[(row.provider_entity_id, row.market_code, row.platform, row.metric_code)].append(row)
        entity_meta[(row.provider_entity_id, row.market_code)] = (row.entity_label, row.artist_label)
        if row.release_date:
            release_dates[row.provider_entity_id] = row.release_date
        if _utc(row.observed_at).timestamp() >= recent_cutoff:
            recent_platforms[(row.provider_entity_id, row.market_code)].add(row.platform)
            if row.market_code != "GLOBAL":
                recent_markets[row.provider_entity_id].add(row.market_code)

    current_platform_counts = {key: len(value) for key, value in recent_platforms.items()}
    current_market_counts = {key: len(value) for key, value in recent_markets.items()}
    platform_cohorts: dict[str, list[float]] = defaultdict(list)
    for (_, market), count in current_platform_counts.items():
        platform_cohorts[market].append(float(count))
    market_count_cohort = [float(value) for value in current_market_counts.values()]

    windows: dict[tuple[str, str, str, str], object] = {}
    velocity_cohorts: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    acceleration_cohorts: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for key, observations in grouped.items():
        if key[3] != "chart_position" and not key[3].startswith("weekly_growth_percent_"):
            continue
        points = [
            Point(
                value=obs.value,
                observed_at=obs.observed_at,
                confidence=obs.confidence if obs.confidence is not None else 0.0,
                coverage=obs.coverage if obs.coverage is not None else 0.0,
            )
            for obs in observations
        ]
        direction = "higher_is_better" if key[3].startswith("weekly_growth_percent_") else "lower_is_better"
        window = calculate_window_metrics(points, "7d", direction=direction, as_of=now)
        windows[key] = window
        cohort_key = (key[1], key[2], key[3])
        if window.velocity_per_day is not None:
            velocity_cohorts[cohort_key].append(window.velocity_per_day)
        if window.acceleration_per_day2 is not None:
            acceleration_cohorts[cohort_key].append(window.acceleration_per_day2)

    # Keep provider-reported weekly percentage changes source-attributed, then normalize
    # within the matching metric cohort for the score features.
    latest_growth_by_entity_metric: dict[tuple[str, str], MetricSnapshot] = {}
    for key, observations in grouped.items():
        metric = key[3]
        if key[1] != "GLOBAL" or not metric.startswith("weekly_growth_percent_"):
            continue
        latest = max(observations, key=lambda item: _utc(item.observed_at))
        latest_growth_by_entity_metric[(key[0], metric)] = latest
    rank_cohorts: dict[str, list[float]] = defaultdict(list)
    for (_, metric), observation in latest_growth_by_entity_metric.items():
        rank_cohorts[metric].append(observation.value)
    relative_growth_scores: dict[str, list[float]] = defaultdict(list)
    for (entity_id, metric), observation in latest_growth_by_entity_metric.items():
        score = percentile_rank(
            observation.value,
            rank_cohorts[metric],
            higher_is_better=metric.startswith("weekly_growth_percent_"),
            minimum_cohort=MIN_COHORT,
        )
        if score is not None:
            relative_growth_scores[entity_id].append(score)

    entity_market_keys = {
        (key[0], key[1]) for key in windows
    }
    rows_written = 0
    scores_created = 0
    scores_by_type = {score_type: 0 for score_type in SCORED_TYPES}
    for entity_id, market_code in sorted(entity_market_keys):
        relevant = [
            (key, window)
            for key, window in windows.items()
            if key[0] == entity_id and key[1] == market_code
        ]
        base, platform_count, observation_count = _normalized_window_features(
            entity_id=entity_id,
            market_code=market_code,
            relevant=relevant,
            grouped=grouped,
            velocity_cohorts=velocity_cohorts,
            acceleration_cohorts=acceleration_cohorts,
            platform_counts=recent_platforms,
            platform_count_cohorts=platform_cohorts,
            market_counts=current_market_counts,
            market_count_cohort=market_count_cohort,
            now=now,
        )
        relative_growth = mean(relative_growth_scores[entity_id]) if relative_growth_scores.get(entity_id) else None
        release_date = release_dates.get(entity_id)
        release_age = (now.date() - release_date).days if release_date else None
        release_recency = max(0.0, 1 - release_age / 90) if release_age is not None and 0 <= release_age <= 90 else None
        trend_features = {
            **base,
            "social_momentum": None,
            "streaming_momentum": None,
            "discovery_momentum": None,
            "playlist_momentum": None,
        }
        breakout_features = {
            "relative_growth": relative_growth,
            "absolute_growth": None,
            "velocity": base["velocity"],
            "acceleration": base["acceleration"],
            "cross_platform": base["cross_platform"],
            "geo_expansion": base["geo_expansion"],
            "playlist_velocity": None,
            "social_velocity": None,
            "video_velocity": None,
            "search_velocity": None,
            "release_freshness": release_recency,
            "growth_persistence": base["persistence"],
        }
        next_features = {
            "acceleration": base["acceleration"],
            "relative_growth": relative_growth,
            "absolute_growth": None,
            "cross_platform_growth": base["cross_platform"],
            "release_recency": release_recency,
            "early_geo_spread": base["geo_expansion"],
            "social_growth": None,
            "discovery_growth": None,
            "search_growth": None,
            "audience_conversion": None,
            "genre_momentum": None,
            "market_momentum": base["velocity"],
        }
        definitions = {
            "trend": (trend_features, {"single_source_dependency": 1.0}),
            "breakout": (
                breakout_features,
                {
                    "single_platform_spike": 1.0 if platform_count <= 1 else 0.0,
                    "low_persistence": 1 - base["persistence"] if base["persistence"] is not None else None,
                    "low_confidence": 1.0,
                },
            ),
            "next": (
                next_features,
                {
                    "single_platform_spike": 1.0 if platform_count <= 1 else 0.0,
                    "low_confidence": 1.0,
                },
            ),
        }
        entity_label, artist_label = entity_meta.get((entity_id, market_code), (None, None))
        for score_type, (features, penalties) in definitions.items():
            confidence_inputs = {key: 0.0 for key, value in features.items() if value is not None}
            result = calculate_score(score_type, features, penalties=penalties, feature_confidence=confidence_inputs)
            evidence = {
                "source_provider": "Chartmetric",
                "source_provider_count": 1,
                "provider_reported_confidence": None,
                "confidence_note": "Chartmetric does not report per-observation confidence/coverage for these chart/metric records.",
                "cohort_minimum": MIN_COHORT,
                "cohort_method": "percentile within matching market/platform/metric; tied values use midrank",
                "rank_is_not_stream_volume": True,
                "relative_growth_is_cohort_percentile": True,
                "relative_growth_metric_codes": sorted(
                    metric
                    for (candidate_id, metric) in latest_growth_by_entity_metric
                    if candidate_id == entity_id
                ),
                "signals": [key for key, value in features.items() if value is not None],
                "observation_count": observation_count,
                "release_age_days": release_age,
                "release_window_eligible": release_age is not None and 0 <= release_age <= 90,
            }
            session.add(IntelligenceScore(
                score_type=score_type,
                entity_type="track",
                provider_entity_id=entity_id,
                entity_label=entity_label,
                artist_label=artist_label,
                market_code=market_code,
                score_value=result.value,
                confidence=result.confidence,
                evidence_coverage=result.evidence_coverage,
                status=result.status,
                algorithm_version=result.algorithm_version,
                feature_version=result.feature_version,
                weights_version=result.weights_version,
                components={**result.components, "penalties": result.penalties},
                evidence=evidence,
                calculated_at=now,
            ))
            rows_written += 1
            if result.value is not None:
                scores_created += 1
                scores_by_type[score_type] += 1

    session.flush()
    return {
        "scores_created": scores_created,
        "scores_by_type": scores_by_type,
        "score_rows_written": rows_written,
        "series_evaluated": len(windows),
        "observations": len(rows),
    }
