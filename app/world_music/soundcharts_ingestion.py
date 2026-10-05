from __future__ import annotations

from datetime import date, datetime, timezone
import math
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MetricSnapshot, ProviderSyncRun, now_utc
from .free_access import soundcharts_free_trial_enabled
from .provider_ingest import soundcharts_free_calls_remaining
from .providers.soundcharts import (
    SoundchartsAPIError,
    SoundchartsFreeAccessRequired,
    SoundchartsProvider,
    SoundchartsRightsNotConfirmed,
)


MAX_SOUNDCHARTS_REQUESTS = 100
DOCS_URL = "https://developers.soundcharts.com/api/reference/charts/get-song-ranking-latest"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value[:10])
        except ValueError:
            return None
        return datetime.combine(parsed_date, datetime.min.time(), timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:36] or "chart"


def _source_url(value: Any) -> str:
    if isinstance(value, str) and value.startswith("https://") and urlparse(value).netloc:
        return value[:500]
    return DOCS_URL


def normalize_soundcharts_chart(
    payload: dict[str, Any],
    *,
    requested_slug: str,
    captured_at: datetime,
    run_id: str,
) -> tuple[list[MetricSnapshot], str]:
    """Convert one official Soundcharts chart response to attributed observations."""
    related = payload.get("related") if isinstance(payload.get("related"), dict) else {}
    chart = related.get("chart") if isinstance(related.get("chart"), dict) else {}
    chart_slug = str(chart.get("slug") or requested_slug)
    chart_key = _safe_slug(chart_slug)
    platform = str(chart.get("platform") or "unknown").strip().casefold()[:80]
    raw_market = str(chart.get("countryCode") or "").strip().upper()
    market = raw_market if len(raw_market) == 2 and raw_market.isalpha() else "GLOBAL"
    chart_url = _source_url(chart.get("webUrl"))
    chart_metric = chart.get("metric") if isinstance(chart.get("metric"), dict) else {}
    metric_type = _safe_slug(str(chart_metric.get("type") or "reported_metric"))[:24]
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise ValueError("Soundcharts chart response has no items list")

    snapshots: list[MetricSnapshot] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        song = item.get("song") if isinstance(item.get("song"), dict) else {}
        entity_id = song.get("uuid")
        observed_at = _timestamp(item.get("rankDate") or related.get("date"))
        if not isinstance(entity_id, str) or not entity_id.strip() or observed_at is None:
            continue
        title = song.get("name") if isinstance(song.get("name"), str) else None
        artist = song.get("creditName") if isinstance(song.get("creditName"), str) else None
        rank = _number(item.get("position"))
        if rank == 0:
            rank = None
        metric_value = _number(item.get("metric"))
        for code, value, unit in (
            (f"chart_position_{chart_key}", rank, "chart_rank"),
            (f"chart_metric_{metric_type}_{chart_key}", metric_value, f"provider_{metric_type}"[:40]),
        ):
            if value is None:
                continue
            snapshots.append(MetricSnapshot(
                provider_name="Soundcharts",
                provider_entity_id=entity_id.strip()[:160],
                entity_type="track",
                entity_label=title[:240] if title else None,
                artist_label=artist[:240] if artist else None,
                metric_code=code[:80],
                platform=platform,
                market_code=market,
                value=value,
                unit=unit,
                observed_at=observed_at,
                captured_at=captured_at,
                confidence=None,
                coverage=None,
                source_url=chart_url,
                rights_basis="licensed",
                sync_run_id=run_id,
            ))
    return snapshots, market


def sync_soundcharts_charts(
    session: Session,
    provider: SoundchartsProvider,
    *,
    chart_slugs: list[str],
    max_requests: int = 10,
    limit: int = 100,
) -> ProviderSyncRun:
    """Capture explicitly selected licensed Soundcharts chart rankings.

    It stores only normalized rank/metric observations; raw vendor payloads are
    discarded. Charts are selected explicitly so the app does not silently
    expand trial-quota consumption across every market or chart category.
    """
    if not provider.rights_confirmed:
        raise SoundchartsRightsNotConfirmed(
            "No Soundcharts data fetched or saved: written rights for local snapshots and playlist research are required."
        )
    if not soundcharts_free_trial_enabled():
        raise SoundchartsFreeAccessRequired(
            "Soundcharts is limited to its explicitly activated free 1,000-request API trial; no request was made."
        )
    remaining = soundcharts_free_calls_remaining(session)
    if remaining <= 0:
        raise SoundchartsAPIError(429, "LOCAL_FREE_TRIAL_REQUEST_CAP_REACHED")
    slugs = list(dict.fromkeys(slug.strip() for slug in chart_slugs if slug.strip()))
    if not slugs:
        raise ValueError("at least one chart slug is required")
    effective_budget = min(max_requests, remaining, MAX_SOUNDCHARTS_REQUESTS)
    if not 1 <= max_requests <= MAX_SOUNDCHARTS_REQUESTS or len(slugs) > effective_budget:
        raise ValueError("max_requests must be 1..100 and cover each selected chart within the remaining free-trial quota")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100 per chart")
    provider.set_request_budget(effective_budget)

    captured_at = now_utc()
    run = ProviderSyncRun(
        provider_name="Soundcharts",
        status="RUNNING",
        started_at=captured_at,
        request_count=0,
        observations_written=0,
        markets_requested=[],
        metrics_available=[],
        rate_limit_status="NOT_CHECKED",
        data_confidence=0.0,
    )
    session.add(run)
    session.flush()
    snapshots: list[MetricSnapshot] = []
    pending_keys: set[tuple[str, str, str, str, datetime]] = set()
    errors: list[str] = []
    markets: set[str] = set()

    for slug in slugs:
        try:
            payload = provider.fetch_latest_song_chart(slug, limit=limit)
        except SoundchartsAPIError as exc:
            errors.append(f"{slug[:20]}_{exc.status_code}_{exc.code}")
            if exc.status_code in {401, 403, 429}:
                break
            continue
        chart_snapshots, market = normalize_soundcharts_chart(
            payload,
            requested_slug=slug,
            captured_at=captured_at,
            run_id=run.id,
        )
        markets.add(market)
        for snapshot in chart_snapshots:
            observation_key = (
                snapshot.provider_entity_id,
                snapshot.metric_code,
                snapshot.platform,
                snapshot.market_code,
                snapshot.observed_at,
            )
            if observation_key in pending_keys:
                continue
            exists = session.scalar(select(MetricSnapshot.id).where(
                MetricSnapshot.provider_name == "Soundcharts",
                MetricSnapshot.entity_type == snapshot.entity_type,
                MetricSnapshot.provider_entity_id == snapshot.provider_entity_id,
                MetricSnapshot.metric_code == snapshot.metric_code,
                MetricSnapshot.platform == snapshot.platform,
                MetricSnapshot.market_code == snapshot.market_code,
                MetricSnapshot.observed_at == snapshot.observed_at,
            ).limit(1))
            if exists:
                continue
            pending_keys.add(observation_key)
            snapshots.append(snapshot)

    session.add_all(snapshots)
    run.observations_written = len(snapshots)
    run.request_count = provider.requests_made
    run.markets_requested = sorted(markets)
    run.metrics_available = sorted({row.metric_code for row in snapshots})
    quota = provider.quota_remaining
    run.rate_limit_status = f"QUOTA_REMAINING:{quota}" if quota else "QUOTA_NOT_REPORTED"
    run.status = "PARTIAL" if errors else "SUCCEEDED"
    run.error_code = ";".join(errors)[:80] or None
    run.finished_at = now_utc()
    session.flush()
    return run
