from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CrossBorderPropagation, MetricSnapshot, ProviderSyncRun, now_utc
from .free_access import chartmetric_free_trial_active
from .providers.chartmetric import (
    ChartmetricAPIError,
    ChartmetricFreeTrialRequired,
    ChartmetricProvider,
    ChartmetricRightsNotConfirmed,
    ChartmetricResponse,
    GROWTH_METRICS,
)


MAX_SYNC_CALLS = 100
CHART_PLATFORMS = ("spotify", "apple_music")


def _numeric(value: Any, *, allow_negative: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result if allow_negative or result >= 0 else None


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: Any = payload.get("obj")
    if isinstance(candidates, dict):
        candidates = candidates.get("data", candidates.get("obj", candidates.get("items")))
    if candidates is None:
        candidates = payload.get("data", payload.get("items"))
    return [item for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []


def _track_id(record: dict[str, Any]) -> str | None:
    value = record.get("cm_track") or record.get("id")
    return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else None


def _release_date(record: dict[str, Any]) -> date | None:
    candidates = [record]
    albums = record.get("album") or record.get("albums")
    if isinstance(albums, list):
        candidates.extend(album for album in albums if isinstance(album, dict))
    elif isinstance(albums, dict):
        candidates.append(albums)
    for candidate in candidates:
        raw = candidate.get("release_date") or candidate.get("releaseDate")
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                pass
    return None


def _artist_label(record: dict[str, Any]) -> str | None:
    for key in ("artist", "artists"):
        value = record.get(key)
        if isinstance(value, list):
            names = [row.get("name") for row in value if isinstance(row, dict) and isinstance(row.get("name"), str)]
            if names:
                return ", ".join(names)[:240]
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            return value["name"][:240]
    for key in ("spotify_artist_names", "artist_names"):
        value = record.get(key)
        if isinstance(value, list):
            names = [name.strip() for name in value if isinstance(name, str) and name.strip()]
            if names:
                return ", ".join(names)[:240]
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), timezone.utc)
        except ValueError:
            return None


def _chart_observed_at(record: dict[str, Any]) -> datetime | None:
    points = record.get("rankStats")
    timestamps = [_timestamp(point.get("timestp")) for point in points if isinstance(point, dict)] if isinstance(points, list) else []
    timestamps = [value for value in timestamps if value is not None]
    if timestamps:
        return max(timestamps)
    for key in ("timestp", "date", "chart_date", "added_at", "observed_at"):
        value = _timestamp(record.get(key))
        if value is not None:
            return value
    return None


def _snapshot(
    *,
    provider_entity_id: str,
    entity_label: str | None,
    artist_label: str | None,
    release_date: date | None,
    metric_code: str,
    platform: str,
    market_code: str,
    value: float,
    unit: str,
    observed_at: datetime,
    captured_at: datetime,
    sync_run_id: str,
    source_url: str,
) -> MetricSnapshot:
    return MetricSnapshot(
        provider_name="Chartmetric",
        provider_entity_id=provider_entity_id,
        entity_type="track",
        entity_label=entity_label[:240] if entity_label else None,
        artist_label=artist_label,
        release_date=release_date,
        metric_code=metric_code,
        platform=platform,
        market_code=market_code,
        value=value,
        unit=unit,
        observed_at=observed_at,
        captured_at=captured_at,
        # The public track/chart responses do not provide per-observation confidence or coverage.
        confidence=None,
        coverage=None,
        source_url=source_url,
        rights_basis="licensed",
        sync_run_id=sync_run_id,
    )


def _upsert_propagation(session: Session, snapshots: list[MetricSnapshot]) -> None:
    first_seen: dict[str, dict[str, datetime]] = {}
    for item in snapshots:
        if item.market_code == "GLOBAL" or item.metric_code != "chart_position":
            continue
        market_dates = first_seen.setdefault(item.provider_entity_id, {})
        old = market_dates.get(item.market_code)
        if old is None or item.observed_at < old:
            market_dates[item.market_code] = item.observed_at

    for entity_id, market_dates in first_seen.items():
        if len(market_dates) < 2:
            continue
        ordered = sorted(market_dates.items(), key=lambda row: (row[1], row[0]))
        origin, origin_time = ordered[0]
        for destination, destination_time in ordered[1:]:
            # Equal-day first appearances do not establish a cross-market order.
            if destination == origin or destination_time.date() <= origin_time.date():
                continue
            detected = session.scalar(
                select(CrossBorderPropagation).where(
                    CrossBorderPropagation.provider_entity_id == entity_id,
                    CrossBorderPropagation.origin_market == origin,
                    CrossBorderPropagation.destination_market == destination,
                )
            )
            # The route table predates provider-specific IDs. Never relabel an
            # existing row from another source as Chartmetric evidence.
            if detected is not None and detected.evidence.get("source_provider") != "Chartmetric":
                continue
            elapsed = (destination_time.date() - origin_time.date()).days
            evidence = {
                "method": "first_observed_chart_market_order",
                "source_provider": "Chartmetric",
                "origin_first_seen": origin_time.isoformat(),
                "destination_first_seen": destination_time.isoformat(),
                "causality_established": False,
                "source_provider_count": 1,
            }
            if detected is None:
                session.add(CrossBorderPropagation(
                    provider_entity_id=entity_id,
                    entity_type="track",
                    origin_market=origin,
                    destination_market=destination,
                    first_detected_at=destination_time,
                    days_to_propagate=elapsed,
                    confidence=0.0,
                    evidence=evidence,
                    updated_at=now_utc(),
                ))
            else:
                detected.days_to_propagate = min(detected.days_to_propagate or elapsed, elapsed)
                detected.first_detected_at = min(detected.first_detected_at, destination_time)
                detected.evidence = evidence
                detected.updated_at = now_utc()


def _chart_metric_records(
    response: ChartmetricResponse,
    *,
    platform: str,
    market: str,
    captured_at: datetime,
    sync_run_id: str,
) -> list[MetricSnapshot]:
    snapshots: list[MetricSnapshot] = []
    for row in _items(response.payload):
        entity_id = _track_id(row)
        position = _numeric(row.get("rank"))
        observed_at = _chart_observed_at(row)
        if not entity_id or position is None or position <= 0 or observed_at is None:
            continue
        snapshots.append(_snapshot(
            provider_entity_id=entity_id,
            entity_label=row.get("name") if isinstance(row.get("name"), str) else None,
            artist_label=_artist_label(row),
            release_date=_release_date(row),
            metric_code="chart_position",
            platform=platform,
            market_code=market,
            value=position,
            unit="chart_rank",
            observed_at=observed_at,
            captured_at=captured_at,
            sync_run_id=sync_run_id,
            source_url="https://apidocs.chartmetric.com/reference/tag/charts",
        ))
    return snapshots


def _growth_metric_records(
    response: ChartmetricResponse,
    *,
    metric: str,
    provider_stat: str,
    captured_at: datetime,
    sync_run_id: str,
) -> list[MetricSnapshot]:
    snapshots: list[MetricSnapshot] = []
    for row in _items(response.payload):
        entity_id = _track_id(row)
        diff = row.get("weekly_diff_percent")
        raw_value = diff.get(provider_stat) if isinstance(diff, dict) else None
        if raw_value is None:
            raw_value = row.get(f"weekly_diff_percent_{provider_stat}")
        value = _numeric(raw_value, allow_negative=True)
        if not entity_id or value is None:
            continue
        snapshots.append(_snapshot(
            provider_entity_id=entity_id,
            entity_label=row.get("name") if isinstance(row.get("name"), str) else None,
            artist_label=_artist_label(row),
            release_date=_release_date(row),
            metric_code=f"weekly_growth_percent_{metric}",
            platform=metric.split("_", 1)[0],
            market_code="GLOBAL",
            value=value,
            unit="provider_weekly_diff_percent",
            # This is the provider's current rolling-week statistic sampled at capture time.
            observed_at=captured_at,
            captured_at=captured_at,
            sync_run_id=sync_run_id,
            source_url="https://apidocs.chartmetric.com/reference/tag/track/get/api/track/list/filter",
        ))
    return snapshots


def sync_chartmetric(
    session: Session,
    provider: ChartmetricProvider,
    *,
    market_codes: list[str] | None = None,
    limit: int = 100,
    max_requests: int = 40,
) -> ProviderSyncRun:
    """Store licensed Chartmetric weekly growth deltas and dated charts.

    API responses are never saved as raw payloads. The vendor's written scope must
    explicitly authorize local observations, derived scores, and public-playlist use.
    """
    if not provider.rights_confirmed:
        raise ChartmetricRightsNotConfirmed(
            "No data fetched or saved: obtain written permission for persistent snapshots, derived scores, and playlist curation before setting CHARTMETRIC_RIGHTS_CONFIRMED=true."
        )
    if not chartmetric_free_trial_active():
        raise ChartmetricFreeTrialRequired(
            "Chartmetric is limited to its explicitly configured, active 7-day free API trial; no request was made."
        )
    if not 1 <= max_requests <= MAX_SYNC_CALLS:
        raise ValueError(f"max_requests must be between 1 and {MAX_SYNC_CALLS}")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100 per provider query")
    provider.set_request_budget(max_requests)
    markets = list(dict.fromkeys(code.strip().upper() for code in (market_codes or []) if len(code.strip()) == 2 and code.strip().isalpha()))
    markets = ["GLOBAL", *[code for code in markets if code != "GLOBAL"]]
    captured_at = now_utc()
    run = ProviderSyncRun(
        provider_name="Chartmetric",
        status="RUNNING",
        started_at=captured_at,
        request_count=0,
        observations_written=0,
        markets_requested=markets,
        metrics_available=[],
        rate_limit_status="NOT_CHECKED",
        data_confidence=0.0,
    )
    session.add(run)
    session.flush()
    snapshots: list[MetricSnapshot] = []
    errors: list[str] = []

    for stat, metric in GROWTH_METRICS.items():
        if provider.requests_made >= max_requests:
            errors.append("REQUEST_BUDGET_REACHED")
            break
        try:
            response = provider.fetch_growth_tracks(stat, limit=limit)
            snapshots.extend(_growth_metric_records(
                response,
                metric=stat,
                provider_stat=metric,
                captured_at=captured_at,
                sync_run_id=run.id,
            ))
            run.rate_limit_status = f"RATE_LIMIT_REMAINING:{response.rate_limit}" if response.rate_limit else "RESPONSE_OK_LIMIT_UNKNOWN"
        except ChartmetricAPIError as exc:
            errors.append(f"GROWTH_{stat}_{exc.status_code}_{exc.code}")
            if exc.status_code in {401, 429}:
                break

    if not errors or "REQUEST_BUDGET_REACHED" not in errors:
        for market in markets:
            for platform in CHART_PLATFORMS:
                if provider.requests_made >= max_requests:
                    errors.append("REQUEST_BUDGET_REACHED")
                    break
                try:
                    response = provider.fetch_chart(platform, market, limit=limit)
                    snapshots.extend(_chart_metric_records(
                        response,
                        platform=platform,
                        market=market,
                        captured_at=captured_at,
                        sync_run_id=run.id,
                    ))
                    run.rate_limit_status = f"RATE_LIMIT_REMAINING:{response.rate_limit}" if response.rate_limit else "RESPONSE_OK_LIMIT_UNKNOWN"
                except ChartmetricAPIError as exc:
                    errors.append(f"{market}_{platform}_{exc.status_code}_{exc.code}")
                    if exc.status_code in {401, 429}:
                        break
            if provider.requests_made >= max_requests:
                break

    unique: dict[tuple[str, str, str, str, str], MetricSnapshot] = {}
    for item in snapshots:
        key = (item.provider_entity_id, item.metric_code, item.platform, item.market_code, item.observed_at.isoformat())
        unique[key] = item
    snapshots = list(unique.values())
    session.add_all(snapshots)
    _upsert_propagation(session, snapshots)
    run.observations_written = len(snapshots)
    run.request_count = provider.requests_made
    run.metrics_available = sorted({row.metric_code for row in snapshots})
    run.status = "PARTIAL" if errors else "SUCCEEDED"
    run.error_code = ";".join(errors)[:80] or None
    run.finished_at = now_utc()
    session.flush()
    return run
