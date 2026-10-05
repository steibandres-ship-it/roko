from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


ALGORITHM_VERSION = "world-momentum-v1.0"
FEATURE_VERSION = "market-signals-v1.0"
WEIGHTS_VERSION = "balanced-v1.0"
MIN_EVIDENCE_COVERAGE = 0.60


# Weights are normalized over observed inputs, while evidence_coverage reports
# what portion of the intended formula was actually measurable.
WEIGHTS: dict[str, dict[str, float]] = {
    "trend": {
        "velocity": 0.20,
        "acceleration": 0.15,
        "cross_platform": 0.15,
        "geo_expansion": 0.15,
        "social_momentum": 0.05,
        "streaming_momentum": 0.10,
        "discovery_momentum": 0.10,
        "playlist_momentum": 0.05,
        "freshness": 0.025,
        "persistence": 0.025,
    },
    "breakout": {
        "relative_growth": 0.20,
        "absolute_growth": 0.05,
        "velocity": 0.15,
        "acceleration": 0.15,
        "cross_platform": 0.10,
        "geo_expansion": 0.10,
        "playlist_velocity": 0.05,
        "social_velocity": 0.05,
        "video_velocity": 0.05,
        "search_velocity": 0.025,
        "release_freshness": 0.025,
        "growth_persistence": 0.05,
    },
    "elite": {
        "current_relevance": 0.20,
        "recent_absolute_demand": 0.20,
        "recent_velocity": 0.10,
        "release_momentum": 0.10,
        "catalog_strength": 0.10,
        "global_presence": 0.10,
        "market_presence": 0.10,
        "cross_platform_strength": 0.05,
        "freshness": 0.025,
        "longevity": 0.025,
    },
    "next": {
        "acceleration": 0.20,
        "relative_growth": 0.15,
        "absolute_growth": 0.05,
        "cross_platform_growth": 0.10,
        "release_recency": 0.15,
        "early_geo_spread": 0.10,
        "social_growth": 0.05,
        "discovery_growth": 0.05,
        "search_growth": 0.05,
        "audience_conversion": 0.05,
        "genre_momentum": 0.025,
        "market_momentum": 0.025,
    },
    "cross_platform": {
        "source_independence": 0.40,
        "recency": 0.20,
        "data_quality": 0.20,
        "confirmation_strength": 0.20,
    },
    "global_reach": {
        "active_market_count": 0.10,
        "market_growth_velocity": 0.12,
        "cross_border_acceleration": 0.12,
        "regional_spread": 0.10,
        "continent_spread": 0.08,
        "cross_platform_confirmation": 0.12,
        "language_transferability": 0.06,
        "market_diversity": 0.10,
        "streaming_momentum": 0.08,
        "social_momentum": 0.05,
        "authenticity": 0.07,
    },
    "export_potential": {
        "local_velocity": 0.18,
        "local_acceleration": 0.15,
        "cross_platform": 0.12,
        "neighbor_market_signal": 0.14,
        "genre_transferability": 0.10,
        "language_transferability": 0.08,
        "international_social_growth": 0.08,
        "discovery_growth": 0.08,
        "search_growth": 0.07,
    },
    "authenticity": {
        "cross_source_independence": 0.20,
        "geo_consistency": 0.15,
        "data_consistency": 0.20,
        "history_depth": 0.15,
        "playlist_diversity": 0.10,
        "signal_stability": 0.20,
    },
}

PENALTY_WEIGHTS: dict[str, dict[str, float]] = {
    "trend": {"saturation": 0.08, "anomaly": 0.15, "single_source_dependency": 0.10, "low_confidence": 0.15},
    "breakout": {"single_platform_spike": 0.15, "low_persistence": 0.12, "anomaly": 0.18, "saturation": 0.08, "low_confidence": 0.18},
    "elite": {"saturation": 0.12, "anomaly": 0.08, "low_confidence": 0.15},
    "next": {"single_platform_spike": 0.15, "anomaly": 0.15, "saturation": 0.08, "low_confidence": 0.18},
    "global_reach": {"single_market_dependency": 0.12, "single_platform_dependency": 0.12, "suspicious_geo_pattern": 0.20, "low_confidence": 0.15},
    "export_potential": {"single_market_dependency": 0.10, "single_platform_dependency": 0.10, "low_confidence": 0.15},
    # Authenticity is a consistency/review signal. Penalties lower confidence;
    # they do not assert that any stream, artist, or listener is fraudulent.
    "authenticity": {"single_source_spike": 0.18, "abnormal_growth": 0.15, "geo_inconsistency": 0.14, "collapse_after_spike": 0.12, "data_conflicts": 0.18, "playlist_spam": 0.15},
}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score_type: str
    value: float | None
    confidence: float
    evidence_coverage: float
    status: str
    components: dict[str, float | None]
    penalties: dict[str, float]
    algorithm_version: str = ALGORITHM_VERSION
    feature_version: str = FEATURE_VERSION
    weights_version: str = WEIGHTS_VERSION


def _checked_unit(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if not 0 <= numeric <= 1:
        raise ValueError(f"{label} must be normalized to [0, 1], received {numeric}")
    return numeric


def calculate_score(
    score_type: str,
    features: Mapping[str, float | None],
    *,
    penalties: Mapping[str, float] | None = None,
    feature_confidence: Mapping[str, float] | None = None,
    min_evidence_coverage: float = MIN_EVIDENCE_COVERAGE,
) -> ScoreResult:
    """Calculate a transparent 0–100 score from normalized, observed inputs.

    Missing inputs remain missing and lower evidence_coverage; they are never
    silently treated as zero. Raw measurements must first be percentile- or
    rule-normalized within a compatible genre/market/career-stage cohort.
    """
    if score_type not in WEIGHTS:
        raise ValueError(f"unsupported score type: {score_type}")
    weights = WEIGHTS[score_type]
    normalized = {key: _checked_unit(features.get(key), key) for key in weights}
    present = {key: value for key, value in normalized.items() if value is not None}
    intended_weight = sum(weights.values())
    measured_weight = sum(weights[key] for key in present)
    coverage = measured_weight / intended_weight if intended_weight else 0.0
    if coverage < min_evidence_coverage or not present:
        return ScoreResult(score_type, None, 0.0, coverage, "INSUFFICIENT", normalized, {})

    base = sum(float(normalized[key]) * weights[key] for key in present) / measured_weight
    penalty_values: dict[str, float] = {}
    for name, weight in PENALTY_WEIGHTS.get(score_type, {}).items():
        raw = _checked_unit((penalties or {}).get(name), name)
        if raw is not None:
            penalty_values[name] = raw * weight
    penalty = min(sum(penalty_values.values()), 0.75)
    score = round(max(0.0, min(1.0, base - penalty)) * 100, 2)

    confidence_inputs = feature_confidence or {}
    known_confidences = [
        min(max(float(confidence_inputs.get(key, 0.0)), 0.0), 1.0) * weights[key]
        for key in present
    ]
    confidence = round((sum(known_confidences) / measured_weight) * coverage, 4)
    if confidence < 0.35:
        status = "LOW_CONFIDENCE"
    else:
        status = "READY" if coverage >= 0.8 else "PARTIAL"
    return ScoreResult(score_type, score, confidence, round(coverage, 4), status, normalized, penalty_values)


def calculate_scores(
    features: Mapping[str, float | None],
    *,
    penalties: Mapping[str, float] | None = None,
    feature_confidence: Mapping[str, float] | None = None,
) -> dict[str, ScoreResult]:
    return {
        score_type: calculate_score(
            score_type,
            features,
            penalties=penalties,
            feature_confidence=feature_confidence,
        )
        for score_type in WEIGHTS
    }


def authenticity_state(result: ScoreResult, *, independent_sources: int) -> str:
    """Conservative state labels; a single aggregator can never imply VALIDATED."""
    if result.value is None or result.confidence < 0.5:
        return "INSUFFICIENT"
    if result.value < 35:
        return "WATCH"
    if result.value >= 85 and independent_sources >= 2 and result.evidence_coverage >= 0.8:
        return "VALIDATED"
    if result.value < 55:
        return "SUSPICIOUS"
    return "WATCH"


def data_confidence(*, coverage: float, freshness: float, source_quality: float, history_depth: float) -> float:
    values = [_checked_unit(coverage, "coverage"), _checked_unit(freshness, "freshness"), _checked_unit(source_quality, "source_quality"), _checked_unit(history_depth, "history_depth")]
    return round(sum(values) / len(values), 4)
