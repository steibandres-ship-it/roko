from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import unicodedata
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import MetricSnapshot, ProviderSyncRun, now_utc
from .providers.lastfm import (
    COUNTRY_NAMES,
    LastFmAPIError,
    LastFmProvider,
    LastFmResponse,
    LastFmScopeNotConfirmed,
    _track_rows,
)


MAX_SYNC_CALLS = 100
SOURCE_URL = "https://www.last.fm/api/show/geo.getTopTracks"
GLOBAL_SOURCE_URL = "https://www.last.fm/api/show/chart.getTopTracks"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and parsed < float("inf") else None


def _track_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if (parsed.hostname or "").casefold() not in {"last.fm", "www.last.fm"}:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.path.startswith("/music/"):
        return None
    return f"https://www.last.fm{parsed.path}"


def _entity_id(row: dict[str, Any], track_url: str, artist: str, title: str) -> str:
    mbid = row.get("mbid")
    if isinstance(mbid, str) and mbid.strip():
        return mbid.strip()[:160]
    identity = unicodedata.normalize("NFKC", track_url or f"{artist}\0{title}").casefold().strip()
    return f"lastfm:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _snapshots(
    response: LastFmResponse,
    *,
    market: str,
    captured_at: datetime,
    run_id: str,
) -> list[MetricSnapshot]:
    rows: list[MetricSnapshot] = []
    for ordinal, record in enumerate(_track_rows(response.payload), start=1):
        title = record.get("name")
        artist_field = record.get("artist")
        artist = artist_field.get("name") if isinstance(artist_field, dict) else artist_field
        if not isinstance(title, str) or not title.strip() or not isinstance(artist, str) or not artist.strip():
            continue
        track_url = _track_url(record.get("url"))
        # Last.fm's terms require links to the matching catalog record when
        # displaying track data, so malformed or non-Last.fm URLs are omitted.
        if track_url is None:
            continue
        attributes = record.get("@attr") if isinstance(record.get("@attr"), dict) else {}
        rank = _number(record.get("rank") or attributes.get("rank")) or float(ordinal)
        if rank < 1:
            continue
        entity_id = _entity_id(record, track_url, artist.strip(), title.strip())
        common = {
            "provider_name": "Last.fm",
            "provider_entity_id": entity_id,
            "entity_type": "track",
            "entity_label": title.strip()[:240],
            "artist_label": artist.strip()[:240],
            "platform": "lastfm",
            "market_code": market,
            "observed_at": captured_at,
            "captured_at": captured_at,
            "confidence": None,
            "coverage": None,
            "source_url": track_url,
            "rights_basis": "provider_terms",
            "sync_run_id": run_id,
        }
        rows.append(MetricSnapshot(
            metric_code="lastfm_chart_rank",
            value=rank,
            unit="rank",
            **common,
        ))
        playcount = _number(record.get("playcount"))
        if playcount is not None:
            rows.append(MetricSnapshot(
                metric_code="lastfm_chart_playcount",
                value=playcount,
                unit="lastfm_playcount",
                **common,
            ))
    return rows


def sync_lastfm(
    session: Session,
    provider: LastFmProvider,
    *,
    market_codes: list[str] | None = None,
    limit: int = 100,
    max_requests: int = 40,
) -> ProviderSyncRun:
    """Capture Last.fm's documented global and country track charts.

    Country chart playcounts are for Last.fm's last-week chart. The global
    endpoint does not document the playcount window, so the stored value is not
    labeled as weekly. Data are source-attributed, not Spotify metrics. Local
    observations older than 30 days are deleted to bound retained API data.
    """
    if not provider.scope_confirmed:
        raise LastFmScopeNotConfirmed(
            "No data fetched or stored: confirm non-commercial use and the API terms for use outside the EEA first."
        )
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100 per provider query")
    if not 1 <= max_requests <= MAX_SYNC_CALLS:
        raise ValueError(f"max_requests must be between 1 and {MAX_SYNC_CALLS}")
    markets = list(dict.fromkeys(code.strip().upper() for code in (market_codes or []) if code.strip()))
    unsupported = sorted(code for code in markets if code not in COUNTRY_NAMES)
    if unsupported:
        raise ValueError(f"unsupported Last.fm markets: {', '.join(unsupported)}")
    markets = ["GLOBAL", *[code for code in markets if code != "GLOBAL"]]
    captured_at = now_utc()
    local_today = captured_at.astimezone(ZoneInfo("America/Santiago")).date()
    markets_to_fetch: list[str] = []
    for market in markets:
        last_capture = session.scalar(select(func.max(MetricSnapshot.captured_at)).where(
            MetricSnapshot.provider_name == "Last.fm",
            MetricSnapshot.market_code == market,
            MetricSnapshot.metric_code == "lastfm_chart_rank",
        ))
        if last_capture is not None:
            if last_capture.tzinfo is None:
                last_capture = last_capture.replace(tzinfo=timezone.utc)
            if last_capture.astimezone(ZoneInfo("America/Santiago")).date() == local_today:
                continue
        markets_to_fetch.append(market)
    if len(markets_to_fetch) > max_requests:
        raise ValueError("max_requests must cover at least one chart request per uncaptured market")

    provider.set_request_budget(max_requests)
    run = ProviderSyncRun(
        provider_name="Last.fm",
        status="RUNNING" if markets_to_fetch else "SKIPPED",
        started_at=captured_at,
        request_count=0,
        observations_written=0,
        markets_requested=markets_to_fetch,
        metrics_available=[],
        rate_limit_status="NOT_REPORTED_BY_PROVIDER",
        data_confidence=0.0,
    )
    session.add(run)
    session.flush()

    if not markets_to_fetch:
        run.error_code = "ALREADY_CAPTURED_TODAY_SANTIAGO"
        run.finished_at = now_utc()
        session.flush()
        return run

    snapshots: list[MetricSnapshot] = []
    errors: list[str] = []
    for market in markets_to_fetch:
        if provider.requests_made >= max_requests:
            errors.append("REQUEST_BUDGET_REACHED")
            break
        try:
            response = provider.fetch_top_tracks(market, limit=limit)
            records = _snapshots(response, market=market, captured_at=captured_at, run_id=run.id)
            if records:
                snapshots.extend(records)
            else:
                errors.append(f"{market}_NO_ATTRIBUTABLE_TRACKS")
            run.rate_limit_status = "API_DID_NOT_REPORT_CURRENT_RATE_LIMIT"
        except LastFmAPIError as exc:
            errors.append(f"{market}_{exc.code}")
            if exc.code in {"401", "429", "10", "29"}:
                break

    # A sync only runs once per user command; collapse any duplicate chart rows
    # returned by the provider before writing the unique dated observations.
    unique: dict[tuple[str, str, str, str], MetricSnapshot] = {}
    for row in snapshots:
        key = (row.provider_entity_id, row.metric_code, row.market_code, row.observed_at.isoformat())
        unique[key] = row
    snapshots = list(unique.values())
    session.add_all(snapshots)
    cutoff = captured_at - timedelta(days=30)
    session.execute(delete(MetricSnapshot).where(
        MetricSnapshot.provider_name == "Last.fm",
        MetricSnapshot.captured_at < cutoff,
    ))
    run.observations_written = len(snapshots)
    run.request_count = provider.requests_made
    run.metrics_available = sorted({row.metric_code for row in snapshots})
    run.status = "PARTIAL" if errors else "SUCCEEDED"
    run.error_code = ";".join(errors)[:80] or None
    run.finished_at = now_utc()
    session.flush()
    return run
