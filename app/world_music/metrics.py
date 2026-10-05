from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import log1p
from statistics import median
from typing import Iterable, Literal


WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "28d": timedelta(days=28),
    "90d": timedelta(days=90),
}


@dataclass(frozen=True, slots=True)
class Point:
    value: float
    observed_at: datetime
    confidence: float = 1.0
    coverage: float = 1.0


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    current_value: float | None
    prior_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    velocity_per_day: float | None
    acceleration_per_day2: float | None
    persistence: float | None
    confidence: float
    observations: int
    window: str
    direction: str


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _ordered(points: Iterable[Point]) -> list[Point]:
    return sorted(points, key=lambda point: _aware(point.observed_at))


def _latest_at_or_before(points: list[Point], target: datetime) -> Point | None:
    matches = [point for point in points if _aware(point.observed_at) <= target]
    return matches[-1] if matches else None


def _window_delta(points: list[Point], end_at: datetime, window: timedelta, direction: str) -> tuple[Point, Point] | None:
    current = _latest_at_or_before(points, end_at)
    if current is None:
        return None
    prior = _latest_at_or_before(points, end_at - window)
    if prior is None or _aware(prior.observed_at) >= _aware(current.observed_at):
        return None
    # Avoid comparing against a stale point when the requested cadence is unavailable.
    if _aware(current.observed_at) - _aware(prior.observed_at) > window * 1.6:
        return None
    return prior, current


def _directional_change(prior: float, current: float, direction: str) -> float:
    return prior - current if direction == "lower_is_better" else current - prior


def calculate_window_metrics(
    points: Iterable[Point],
    window: str = "7d",
    *,
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better",
    as_of: datetime | None = None,
) -> WindowMetrics:
    """Calculate changes without converting missing baselines to zero.

    `lower_is_better` is for chart ranks, where moving from rank 40 to rank 20
    is positive. Relative change is intentionally undefined when the baseline is 0.
    """
    if window not in WINDOWS:
        raise ValueError(f"unsupported window: {window}")
    ordered = _ordered(points)
    if not ordered:
        return WindowMetrics(None, None, None, None, None, None, None, 0.0, 0, window, direction)
    end_at = _aware(as_of) if as_of else _aware(ordered[-1].observed_at)
    span = WINDOWS[window]
    pair = _window_delta(ordered, end_at, span, direction)
    if pair is None:
        return WindowMetrics(
            current_value=_latest_at_or_before(ordered, end_at).value if _latest_at_or_before(ordered, end_at) else None,
            prior_value=None,
            absolute_delta=None,
            relative_delta=None,
            velocity_per_day=None,
            acceleration_per_day2=None,
            persistence=None,
            confidence=0.0,
            observations=len(ordered),
            window=window,
            direction=direction,
        )
    prior, current = pair
    current_ts, prior_ts = _aware(current.observed_at), _aware(prior.observed_at)
    elapsed_days = max((current_ts - prior_ts).total_seconds() / 86400, 1 / 24)
    raw_delta = _directional_change(prior.value, current.value, direction)
    relative = (raw_delta / abs(prior.value)) if prior.value != 0 else None
    recent_velocity = raw_delta / elapsed_days

    previous_pair = _window_delta(ordered, current_ts - span, span, direction)
    acceleration: float | None = None
    if previous_pair is not None:
        old_prior, old_current = previous_pair
        old_days = max((_aware(old_current.observed_at) - _aware(old_prior.observed_at)).total_seconds() / 86400, 1 / 24)
        older_velocity = _directional_change(old_prior.value, old_current.value, direction) / old_days
        acceleration = (recent_velocity - older_velocity) / max(elapsed_days, 1 / 24)

    # Persistence measures the share of observed consecutive changes that moved
    # in the desired direction; it needs multiple observations to be meaningful.
    recent_points = [point for point in ordered if current_ts - span <= _aware(point.observed_at) <= current_ts]
    persistence = None
    if len(recent_points) >= 3:
        moves = [
            _directional_change(left.value, right.value, direction)
            for left, right in zip(recent_points, recent_points[1:])
        ]
        persistence = sum(move > 0 for move in moves) / len(moves)
    confidence = median([min(max(point.confidence, 0), 1) * min(max(point.coverage, 0), 1) for point in (prior, current)])
    return WindowMetrics(
        current_value=current.value,
        prior_value=prior.value,
        absolute_delta=raw_delta,
        relative_delta=relative,
        velocity_per_day=recent_velocity,
        acceleration_per_day2=acceleration,
        persistence=persistence,
        confidence=confidence,
        observations=len(ordered),
        window=window,
        direction=direction,
    )


def percentile_rank(value: float | None, cohort: Iterable[float], *, higher_is_better: bool = True, minimum_cohort: int = 20) -> float | None:
    """Return a [0, 1] empirical percentile only for a sufficiently sized cohort."""
    members = [member for member in cohort if member is not None]
    if value is None or len(members) < minimum_cohort:
        return None
    less = sum(member < value for member in members)
    equal = sum(member == value for member in members)
    rank = (less + 0.5 * equal) / len(members)
    return rank if higher_is_better else 1.0 - rank


def robust_outlier_ratio(values: Iterable[float]) -> float | None:
    """Robustly flag an extreme upper-tail signal relative to its cohort.

    Returns a ratio based on median absolute deviation. This is diagnostic input,
    not a claim that activity is artificial.
    """
    sample = [float(value) for value in values]
    if len(sample) < 20:
        return None
    center = median(sample)
    deviation = median([abs(value - center) for value in sample])
    if deviation == 0:
        return 0.0 if center == 0 else max(0.0, log1p(max(sample) / abs(center)))
    return max(0.0, (max(sample) - center) / (1.4826 * deviation))
