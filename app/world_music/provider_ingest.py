from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import os
import re
from typing import Literal
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from ..config import PROJECT_ROOT
from .free_access import (
    SOUNDCHARTS_FREE_REQUEST_CAP,
    chartmetric_free_trial_active,
    soundcharts_free_trial_enabled,
)
from .models import MetricSnapshot, ProviderSyncRun, now_utc
from .providers.base import ProviderStatus


PROVIDER_NAMES = ("Soundcharts", "Chartmetric", "Last.fm")
RIGHTS_FLAGS = {
    "Soundcharts": ("SOUNDCHARTS_RIGHTS_CONFIRMED",),
    "Chartmetric": ("CHARTMETRIC_RIGHTS_CONFIRMED",),
    "Last.fm": ("LASTFM_NONCOMMERCIAL_USE", "LASTFM_NON_EEA_PERMISSION_CONFIRMED"),
}
RIGHTS_BASIS = {"Soundcharts": "licensed", "Chartmetric": "licensed", "Last.fm": "provider_terms"}
MAX_OBSERVATIONS = 2_000
MAX_REQUESTS = 100
LASTFM_RETENTION_DAYS = 30
_SOURCE_HOSTS = {
    "Soundcharts": {"soundcharts.com"},
    "Chartmetric": {"chartmetric.com"},
    "Last.fm": {"last.fm"},
}
_LASTFM_METRICS = {
    "lastfm_chart_rank": "rank",
    "lastfm_chart_playcount": "lastfm_playcount",
}
_CHARTMETRIC_METRICS = {
    "chart_position": "chart_rank",
    "weekly_growth_percent_spotify_plays": "provider_weekly_diff_percent",
    "weekly_growth_percent_tiktok_posts": "provider_weekly_diff_percent",
    "weekly_growth_percent_youtube_views": "provider_weekly_diff_percent",
    "weekly_growth_percent_shazam_count": "provider_weekly_diff_percent",
}


def settings() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(PROJECT_ROOT / ".env").items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


def ingest_api_token() -> str:
    return settings().get("WORLD_MUSIC_INGEST_API_TOKEN", "").strip()


def provider_rights_enabled(provider_name: str) -> bool:
    values = settings()
    flags = RIGHTS_FLAGS.get(provider_name, ())
    return bool(flags) and all(values.get(flag, "false").strip().casefold() in {"1", "true", "yes"} for flag in flags)


def provider_free_access_enabled(provider_name: str) -> bool:
    if provider_name == "Soundcharts":
        return soundcharts_free_trial_enabled()
    if provider_name == "Chartmetric":
        return chartmetric_free_trial_active()
    return provider_name == "Last.fm"


def soundcharts_free_calls_used(session: Session) -> int:
    used = session.scalar(
        select(func.coalesce(func.sum(ProviderSyncRun.request_count), 0)).where(
            ProviderSyncRun.provider_name == "Soundcharts"
        )
    )
    return max(int(used or 0), 0)


def soundcharts_free_calls_remaining(session: Session) -> int:
    return max(SOUNDCHARTS_FREE_REQUEST_CAP - soundcharts_free_calls_used(session), 0)


def merge_provider_sync_statuses(session: Session, statuses: list[ProviderStatus]) -> list[ProviderStatus]:
    """Overlay recent authenticated bridge runs without overstating source confidence."""
    provider_names = {status.provider_name for status in statuses}
    runs = session.scalars(
        select(ProviderSyncRun)
        .where(ProviderSyncRun.provider_name.in_(provider_names))
        .order_by(ProviderSyncRun.started_at.desc())
    ).all()
    latest_by_provider: dict[str, ProviderSyncRun] = {}
    for run in runs:
        latest_by_provider.setdefault(run.provider_name, run)

    now = now_utc()
    result = []
    for status in statuses:
        run = latest_by_provider.get(status.provider_name)
        if status.provider_name == "Soundcharts":
            remaining = soundcharts_free_calls_remaining(session)
            if remaining == 0 and status.state in {"CONNECTED", "DEGRADED", "CONFIGURED_UNVERIFIED"}:
                result.append(status.model_copy(update={
                    "state": "DEGRADED",
                    "rate_limit_status": "FREE_TRIAL_REQUEST_CAP_REACHED",
                    "setup_action": "The local 1,000-request free-trial budget is exhausted; this free-only setup will not switch to a paid plan.",
                    "freshness_note": "No further Soundcharts chart requests are allowed by the local free-trial cap.",
                }))
                continue
        if run is None or not provider_rights_enabled(status.provider_name) or not provider_free_access_enabled(status.provider_name):
            result.append(status)
            continue
        last_attempt = run.finished_at or run.started_at
        last_attempt = last_attempt.replace(tzinfo=timezone.utc) if last_attempt.tzinfo is None else last_attempt.astimezone(timezone.utc)
        fresh = now - last_attempt <= timedelta(hours=72)
        connected = run.status in {"SUCCEEDED", "SUCCESS"} and fresh
        result.append(status.model_copy(update={
            "state": "CONNECTED" if connected else "DEGRADED",
            "country_coverage": sorted(set(status.country_coverage) | set(run.markets_requested or [])),
            "metrics_available": run.metrics_available or status.metrics_available,
            "last_refresh": last_attempt,
            "rate_limit_status": (
                f"{run.rate_limit_status};LOCAL_TRIAL_REQUESTS_REMAINING:{soundcharts_free_calls_remaining(session)}"
                if status.provider_name == "Soundcharts"
                else run.rate_limit_status
            ),
            "setup_action": None if connected else "Review the latest external provider bridge run and its reported limits/errors.",
            "freshness_note": (
                "External bridge run recorded; freshness is determined by each provider observation timestamp."
                if fresh else "External bridge data is older than 72 hours; run the authorized provider sync again."
            ),
        }))
    return result


class ProviderObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_entity_id: str = Field(min_length=1, max_length=160)
    entity_type: Literal["track", "artist", "playlist", "genre", "market"] = "track"
    entity_label: str | None = Field(default=None, max_length=240)
    artist_label: str | None = Field(default=None, max_length=240)
    release_date: date | None = None
    metric_code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    platform: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")
    market_code: str = Field(min_length=2, max_length=80, pattern=r"^(GLOBAL|[A-Z]{2})$")
    value: float
    unit: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    observed_at: datetime
    confidence: float | None = Field(default=None, ge=0, le=1)
    coverage: float | None = Field(default=None, ge=0, le=1)
    source_url: str | None = Field(default=None, max_length=500)

    @field_validator("provider_entity_id", "entity_label", "artist_label")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("value")
    @classmethod
    def require_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value

    @field_validator("source_url")
    @classmethod
    def require_safe_https_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source URL must be HTTPS")
        if parsed.query or parsed.fragment:
            raise ValueError("source URL must not contain query parameters or a fragment")
        return value


class ProviderBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_name: Literal["Soundcharts", "Chartmetric", "Last.fm"]
    status: Literal["SUCCEEDED", "PARTIAL", "FAILED", "SKIPPED"]
    request_count: int = Field(ge=0, le=MAX_REQUESTS)
    markets_requested: list[str] = Field(default_factory=list, max_length=100)
    rate_limit_status: str = Field(default="NOT_REPORTED", min_length=1, max_length=80, pattern=r"^[A-Z0-9_:.-]+$")
    error_code: str | None = Field(default=None, max_length=80, pattern=r"^[A-Z0-9_:;.-]+$")
    observations: list[ProviderObservationInput] = Field(default_factory=list, max_length=MAX_OBSERVATIONS)

    @field_validator("markets_requested")
    @classmethod
    def validate_markets(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(not re.fullmatch(r"GLOBAL|[A-Z]{2}", value) for value in normalized):
            raise ValueError("markets must be GLOBAL or ISO alpha-2 codes")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_provider_observations(self) -> "ProviderBatchInput":
        if self.status == "FAILED" and self.observations:
            raise ValueError("failed batches cannot contain observations")
        if self.observations and self.request_count < 1:
            raise ValueError("provider observations require at least one reported source request")
        for row in self.observations:
            if self.provider_name == "Last.fm":
                expected_unit = _LASTFM_METRICS.get(row.metric_code)
                if row.platform != "lastfm" or row.source_url is None:
                    raise ValueError("Last.fm rows require Last.fm platform and attribution URL")
                if row.metric_code == "lastfm_chart_rank" and row.market_code == "GLOBAL" and row.value < 1:
                    raise ValueError("chart ranks start at one")
                if row.metric_code == "lastfm_chart_playcount" and row.value < 0:
                    raise ValueError("playcounts cannot be negative")
            elif self.provider_name == "Chartmetric":
                expected_unit = _CHARTMETRIC_METRICS.get(row.metric_code)
                if row.platform not in {"spotify", "apple_music", "tiktok", "youtube", "shazam"}:
                    raise ValueError("unsupported Chartmetric platform")
                if row.metric_code.startswith("weekly_growth_percent_") and row.value < -100:
                    raise ValueError("weekly growth delta is outside a plausible percentage range")
            else:
                if row.metric_code.startswith("chart_position_"):
                    expected_unit = "chart_rank"
                elif re.fullmatch(r"chart_metric_[a-z0-9_]+_[a-z0-9_]+", row.metric_code):
                    expected_unit = row.unit if re.fullmatch(r"provider_[a-z0-9_]+", row.unit) else None
                else:
                    expected_unit = None
                if row.value < 0:
                    raise ValueError("Soundcharts metrics cannot be negative")

            if expected_unit is None or row.unit != expected_unit:
                raise ValueError("metric code and unit are not supported for this provider")
            if not row.source_url:
                raise ValueError("source URL is required")
            hostname = (urlparse(row.source_url).hostname or "").casefold()
            if not any(hostname == domain or hostname.endswith("." + domain) for domain in _SOURCE_HOSTS[self.provider_name]):
                raise ValueError("source URL domain does not match provider")
        return self


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _observation_key(provider_name: str, row: ProviderObservationInput) -> tuple:
    return (
        provider_name,
        row.entity_type,
        row.provider_entity_id,
        row.metric_code,
        row.platform,
        row.market_code,
        _utc(row.observed_at).replace(tzinfo=None),
    )


def persist_provider_batch(session: Session, batch: ProviderBatchInput) -> dict[str, int | str]:
    if not provider_free_access_enabled(batch.provider_name):
        raise PermissionError(f"{batch.provider_name} free access mode is not active in the receiving app")
    if not provider_rights_enabled(batch.provider_name):
        raise PermissionError(f"{batch.provider_name} rights flags are not enabled in the receiving app")
    if batch.provider_name == "Soundcharts" and batch.request_count > soundcharts_free_calls_remaining(session):
        raise FreeProviderBudgetExceeded("Soundcharts local free-trial request cap would be exceeded")

    received_at = now_utc()
    run = ProviderSyncRun(
        provider_name=batch.provider_name,
        status=batch.status,
        started_at=received_at,
        finished_at=received_at,
        request_count=batch.request_count,
        observations_written=0,
        markets_requested=batch.markets_requested,
        metrics_available=sorted({row.metric_code for row in batch.observations}),
        rate_limit_status=batch.rate_limit_status,
        # Providers do not report a cross-source confidence value for this batch.
        data_confidence=0.0,
        error_code=batch.error_code,
    )
    session.add(run)
    session.flush()

    new_rows: list[MetricSnapshot] = []
    duplicate_count = 0
    seen: set[tuple] = set()
    for offset in range(0, len(batch.observations), 80):
        chunk = batch.observations[offset:offset + 80]
        candidates = [(row, _observation_key(batch.provider_name, row)) for row in chunk]
        distinct_candidates = [(row, key) for row, key in candidates if key not in seen]
        duplicate_count += len(candidates) - len(distinct_candidates)
        seen.update(key for _, key in distinct_candidates)
        if not distinct_candidates:
            continue
        keys = [key[1:] for _, key in distinct_candidates]
        existing = session.execute(
            select(
                MetricSnapshot.entity_type,
                MetricSnapshot.provider_entity_id,
                MetricSnapshot.metric_code,
                MetricSnapshot.platform,
                MetricSnapshot.market_code,
                MetricSnapshot.observed_at,
            ).where(tuple_(
                MetricSnapshot.entity_type,
                MetricSnapshot.provider_entity_id,
                MetricSnapshot.metric_code,
                MetricSnapshot.platform,
                MetricSnapshot.market_code,
                MetricSnapshot.observed_at,
            ).in_(keys))
            .where(MetricSnapshot.provider_name == batch.provider_name)
        ).all()
        existing_keys = {
            (
                batch.provider_name,
                row.entity_type,
                row.provider_entity_id,
                row.metric_code,
                row.platform,
                row.market_code,
                _utc(row.observed_at).replace(tzinfo=None),
            )
            for row in existing
        }
        for observation, key in distinct_candidates:
            if key in existing_keys:
                duplicate_count += 1
                continue
            new_rows.append(MetricSnapshot(
                provider_name=batch.provider_name,
                provider_entity_id=observation.provider_entity_id,
                entity_type=observation.entity_type,
                entity_label=observation.entity_label,
                artist_label=observation.artist_label,
                release_date=observation.release_date,
                metric_code=observation.metric_code,
                platform=observation.platform,
                market_code=observation.market_code,
                value=observation.value,
                unit=observation.unit,
                observed_at=observation.observed_at,
                captured_at=received_at,
                confidence=observation.confidence,
                coverage=observation.coverage,
                source_url=observation.source_url,
                rights_basis=RIGHTS_BASIS[batch.provider_name],
                sync_run_id=run.id,
            ))

    session.add_all(new_rows)
    run.observations_written = len(new_rows)
    if batch.provider_name == "Last.fm":
        # Respect the integration's bounded cache window for Last.fm data.
        session.query(MetricSnapshot).filter(
            MetricSnapshot.provider_name == "Last.fm",
            MetricSnapshot.captured_at < received_at - timedelta(days=LASTFM_RETENTION_DAYS),
        ).delete(synchronize_session=False)
    session.flush()
    score_result: dict[str, int] | None = None
    if batch.provider_name == "Chartmetric" and batch.status in {"SUCCEEDED", "PARTIAL"}:
        from .ingestion import _upsert_propagation
        from .score_engine import recalculate_trend_scores

        _upsert_propagation(session, new_rows)
        score_result = recalculate_trend_scores(session)
    result: dict[str, int | str] = {
        "run_id": run.id,
        "status": run.status,
        "observations_received": len(batch.observations),
        "observations_written": len(new_rows),
        "duplicates_skipped": duplicate_count,
    }
    if score_result is not None:
        result["score_rows_written"] = score_result["score_rows_written"]
        result["scores_created"] = score_result["scores_created"]
    return result


class FreeProviderBudgetExceeded(RuntimeError):
    pass
