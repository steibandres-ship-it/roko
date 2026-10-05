from __future__ import annotations

from datetime import date, datetime, timezone
import hmac
import ipaddress
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import distinct, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from pathlib import Path

from ..playlists import load_playlists
from ..playlists import get_playlist_config, PlaylistConfigError
from ..spotify_client import SpotifyAPIError, SpotifyClient
from ..auth import OAuthManager, TokenStore
from .db import get_session
from .free_access import SOUNDCHARTS_FREE_REQUEST_CAP
from .models import (
    Artist,
    CrossBorderPropagation,
    Genre,
    IntelligenceScore,
    Market,
    MetricSnapshot,
    OrganicCampaignReport,
    ProviderSyncRun,
    Release,
    Track,
    TrackGenre,
    TrackMarket,
)
from .providers.registry import ProviderRegistry
from .signal_order import build_signal_order
from .provider_comparison import build_provider_comparison
from .public_playlists import generate_public_playlist_drafts, public_playlist_registry_view
from .lastfm_review import MAX_REVIEW_CANDIDATES, generate_lastfm_playlist_review
from .providers.lastfm import LastFmProvider
from .playlist_blueprints import playlist_blueprint_registry_view
from .playlist_launch import build_blueprint_launch_plans
from .campaigns import campaign_registry_view, campaign_report_view
from .provider_ingest import (
    FreeProviderBudgetExceeded,
    ProviderBatchInput,
    ingest_api_token,
    merge_provider_sync_statuses,
    persist_provider_batch,
    provider_rights_enabled,
    provider_free_access_enabled,
    soundcharts_free_calls_remaining,
)
from ..auth import TokenStore
from ..config import load_settings


app = FastAPI(
    title="WORLD MUSIC OS",
    version="0.1.0",
    description="Local-first music discovery data foundation. Spotify is a publication destination, not a metrics provider. An authenticated external bridge can submit licensed provider observations.",
)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class OrganicCampaignReportInput(BaseModel):
    channel: str = Field(min_length=2, max_length=60)
    source_name: str = Field(min_length=2, max_length=120)
    followers_source: str | None = Field(default=None, max_length=120)
    period_start: date
    period_end: date
    views: int | None = Field(default=None, ge=0, le=2_000_000_000)
    reach: int | None = Field(default=None, ge=0, le=2_000_000_000)
    link_clicks: int | None = Field(default=None, ge=0, le=2_000_000_000)
    followers_start: int | None = Field(default=None, ge=0, le=2_000_000_000)
    followers_end: int | None = Field(default=None, ge=0, le=2_000_000_000)
    notes: str | None = Field(default=None, max_length=500)


def _timestamp_text(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _latest_score_rows():
    return select(
        IntelligenceScore.id.label("score_id"),
        IntelligenceScore.score_type.label("score_type"),
        IntelligenceScore.score_value.label("score_value"),
        func.row_number().over(
            partition_by=(
                IntelligenceScore.score_type,
                IntelligenceScore.entity_type,
                IntelligenceScore.provider_entity_id,
                IntelligenceScore.market_code,
            ),
            order_by=IntelligenceScore.calculated_at.desc(),
        ).label("row_number"),
    ).where(
        IntelligenceScore.evidence["source_provider"].as_string() == "Chartmetric",
    ).subquery()


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/api/health", tags=["system"])
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "version": app.version}


def _provider_statuses(session: Session) -> list:
    return merge_provider_sync_statuses(session, ProviderRegistry().statuses())


def _authorize_provider_bridge(request: Request) -> None:
    expected_token = ingest_api_token()
    if len(expected_token) < 32:
        raise HTTPException(status_code=503, detail="Provider ingestion is not configured securely on this server.")
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied_token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not supplied_token or not hmac.compare_digest(
        supplied_token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="A valid bearer token is required.")


def _require_local_same_origin(request: Request, *, require_origin: bool = False) -> None:
    remote_host = request.client.host if request.client else ""
    try:
        local_request = ipaddress.ip_address(remote_host).is_loopback
    except ValueError:
        local_request = False
    if not local_request:
        raise HTTPException(status_code=403, detail="This campaign report route is available only from the local machine.")
    origin = request.headers.get("origin")
    if require_origin and not origin:
        raise HTTPException(status_code=403, detail="A same-origin browser request is required.")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin.rstrip("/") != expected_origin:
        raise HTTPException(status_code=403, detail="Cross-origin campaign report requests are not accepted.")


@app.get("/api/providers/status", tags=["providers"])
def providers_status(session: Session = Depends(get_session)) -> dict:
    try:
        settings = load_settings(require_client_id=False)
        has_session = settings.client_id != "not-configured" and TokenStore(settings.client_id).load() is not None
    except Exception:
        has_session = False
    return {
        "providers": [status.model_dump(mode="json") for status in _provider_statuses(session)],
        "publishing_destination": {
            "name": "Spotify Web API",
            "state": "AUTHORIZATION_SAVED_UNVERIFIED" if has_session else "PROVIDER_NOT_CONNECTED",
            "purpose": ["catalog resolution", "playlist publication"],
            "audience_metrics": False,
            "runtime_access_checked": False,
        },
        "note": "Soundcharts, Chartmetric, and Last.fm are separate metrics providers; Spotify is only a catalog/publication destination.",
    }


@app.get("/api/providers/free-budget", tags=["providers"])
def provider_free_budget(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Return the local Soundcharts free-trial budget to the authenticated bridge."""
    _authorize_provider_bridge(request)
    return {
        "soundcharts": {
            "access_mode_active": provider_free_access_enabled("Soundcharts"),
            "local_trial_request_cap": SOUNDCHARTS_FREE_REQUEST_CAP,
            "local_requests_used": SOUNDCHARTS_FREE_REQUEST_CAP - soundcharts_free_calls_remaining(session),
            "local_requests_remaining": soundcharts_free_calls_remaining(session),
        }
    }


@app.post("/api/providers/ingest", tags=["providers"])
async def ingest_provider_observations(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Accept normalized, rights-gated observations from the separate provider bridge."""
    _authorize_provider_bridge(request)

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 4 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Provider batches are limited to 4 MiB.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Provider batches are limited to 4 MiB.")
    try:
        batch = ProviderBatchInput.model_validate_json(bytes(body))
    except ValidationError as exc:
        safe_errors = [
            {"path": ".".join(str(part) for part in error.get("loc", ()))[:120], "code": str(error.get("type", "invalid"))[:60]}
            for error in exc.errors()[:30]
        ]
        raise HTTPException(status_code=422, detail={"message": "Provider batch validation failed.", "errors": safe_errors}) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON.") from None
    if not provider_rights_enabled(batch.provider_name):
        raise HTTPException(status_code=403, detail=f"Receiving app rights gate is not enabled for {batch.provider_name}.")
    if not provider_free_access_enabled(batch.provider_name):
        raise HTTPException(status_code=403, detail=f"Free access mode is not active for {batch.provider_name}.")
    try:
        result = persist_provider_batch(session, batch)
        session.commit()
        return result
    except PermissionError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except FreeProviderBudgetExceeded as exc:
        session.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Provider batch conflicted with an existing observation; retry only after reviewing the batch.") from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=500, detail="Provider batch could not be stored.") from None


@app.get("/api/world/overview", tags=["world"])
def world_overview(session: Session = Depends(get_session)) -> dict[str, int | str]:
    snapshot_count = session.scalar(
        select(func.count()).select_from(MetricSnapshot)
    ) or 0
    latest_scores = _latest_score_rows()
    score_count = session.scalar(
        select(func.count()).select_from(latest_scores).where(
            latest_scores.c.row_number == 1,
            latest_scores.c.score_value.is_not(None),
        )
    ) or 0
    return {
        "artists_monitored": session.scalar(select(func.count()).select_from(Artist)) or 0,
        "tracks_monitored": session.scalar(select(func.count()).select_from(Track)) or 0,
        "releases_monitored": session.scalar(select(func.count()).select_from(Release)) or 0,
        "genres_cataloged": session.scalar(select(func.count()).select_from(Genre)) or 0,
        "markets_cataloged": session.scalar(select(func.count()).select_from(Market)) or 0,
        "spotify_playlist_definitions": len(load_playlists()),
        "metric_snapshots": snapshot_count,
        "trend_scores_available": score_count,
        "data_status": "READY" if snapshot_count else "NO_PROVIDER_METRICS_CAPTURED",
    }


@app.get("/api/intelligence/overview", tags=["intelligence"])
def intelligence_overview(session: Session = Depends(get_session)) -> dict:
    latest_runs = session.scalars(select(ProviderSyncRun).order_by(ProviderSyncRun.started_at.desc())).all()
    run_by_provider: dict[str, ProviderSyncRun] = {}
    for provider_run in latest_runs:
        run_by_provider.setdefault(provider_run.provider_name, provider_run)
    observation_rows = session.execute(
        select(MetricSnapshot.provider_name, func.count()).group_by(MetricSnapshot.provider_name)
    ).all()
    observations_by_provider = {provider_name: count for provider_name, count in observation_rows}

    def run_payload(provider_run: ProviderSyncRun | None) -> dict | None:
        if provider_run is None:
            return None
        return {
            "status": provider_run.status,
            "started_at": _timestamp_text(provider_run.started_at),
            "finished_at": _timestamp_text(provider_run.finished_at) if provider_run.finished_at else None,
            "request_count": provider_run.request_count,
            "observations_written": provider_run.observations_written,
            "markets_requested": provider_run.markets_requested,
            "metrics_available": provider_run.metrics_available,
            "rate_limit_status": provider_run.rate_limit_status,
            "error_code": provider_run.error_code,
        }
    latest_scores = _latest_score_rows()
    country_codes = session.scalars(
        select(MetricSnapshot.market_code)
        .where(MetricSnapshot.market_code != "GLOBAL")
        .distinct()
        .order_by(MetricSnapshot.market_code)
    ).all()
    return {
        "metric_observations": sum(observations_by_provider.values()),
        "observations_by_provider": observations_by_provider,
        "provider_activity": {
            provider_name: {
                "observations": observations_by_provider.get(provider_name, 0),
                "latest_sync": run_payload(run_by_provider.get(provider_name)),
            }
            for provider_name in ("Soundcharts", "Chartmetric", "Last.fm")
        },
        "trend_scores": session.scalar(select(func.count()).select_from(latest_scores).where(
            latest_scores.c.row_number == 1,
            latest_scores.c.score_type == "trend",
            latest_scores.c.score_value.is_not(None),
        )) or 0,
        "breakout_scores": session.scalar(select(func.count()).select_from(latest_scores).where(
            latest_scores.c.row_number == 1,
            latest_scores.c.score_type == "breakout",
            latest_scores.c.score_value.is_not(None),
        )) or 0,
        "next_scores": session.scalar(select(func.count()).select_from(latest_scores).where(
            latest_scores.c.row_number == 1,
            latest_scores.c.score_type == "next",
            latest_scores.c.score_value.is_not(None),
        )) or 0,
        "markets_with_observations": len(country_codes),
        "observed_markets": country_codes,
        "propagation_routes": session.scalar(
            select(func.count()).select_from(CrossBorderPropagation).where(
                CrossBorderPropagation.evidence["source_provider"].as_string().in_(["Last.fm", "Chartmetric"])
            )
        ) or 0,
        "latest_sync": {
            "status": latest_run.status,
            "started_at": _timestamp_text(latest_run.started_at),
            "finished_at": _timestamp_text(latest_run.finished_at) if latest_run.finished_at else None,
            "request_count": latest_run.request_count,
            "observations_written": latest_run.observations_written,
            "markets_requested": latest_run.markets_requested,
            "metrics_available": latest_run.metrics_available,
            "rate_limit_status": latest_run.rate_limit_status,
            "error_code": latest_run.error_code,
        } if (latest_run := run_by_provider.get("Last.fm")) else None,
    }


@app.get("/api/markets", tags=["markets"])
def list_markets(session: Session = Depends(get_session)) -> list[dict[str, str | None]]:
    rows = session.scalars(select(Market).order_by(Market.scope, Market.name)).all()
    return [
        {"key": row.market_key, "name": row.name, "scope": row.scope, "iso_code": row.iso_code}
        for row in rows
    ]


@app.get("/api/markets/{country_code}", tags=["markets"])
def get_country(country_code: str, session: Session = Depends(get_session)) -> dict[str, str | None]:
    code = country_code.upper()
    if len(code) != 2:
        raise HTTPException(status_code=422, detail="country_code must be an ISO 3166-1 alpha-2 code")
    row = session.scalar(select(Market).where(Market.iso_code == code))
    if row is None:
        raise HTTPException(status_code=404, detail="market not cataloged")
    return {"key": row.market_key, "name": row.name, "scope": row.scope, "iso_code": row.iso_code}


@app.get("/api/markets/{country_code}/genres", tags=["markets"])
def country_genres(country_code: str, session: Session = Depends(get_session)) -> list[dict[str, int | float | str]]:
    code = country_code.upper()
    market = session.scalar(select(Market).where(Market.iso_code == code))
    if market is None:
        raise HTTPException(status_code=404, detail="market not cataloged")
    rows = session.execute(
        select(Genre.slug, Genre.name, func.count(distinct(Track.id)))
        .join(TrackGenre, TrackGenre.genre_id == Genre.id)
        .join(Track, Track.id == TrackGenre.track_id)
        .join(TrackMarket, TrackMarket.track_id == Track.id)
        .where(TrackMarket.market_id == market.id)
        .group_by(Genre.slug, Genre.name)
        .order_by(func.count(distinct(Track.id)).desc(), Genre.name)
    ).all()
    return [{"slug": slug, "name": name, "catalog_track_count": count} for slug, name, count in rows]


@app.get("/api/markets/{country_code}/tracks", tags=["markets"])
def country_chart_tracks(
    country_code: str,
    platform: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[dict]:
    code = country_code.upper()
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(status_code=422, detail="country_code must be an ISO alpha-2 code")
    query = select(MetricSnapshot).where(
        MetricSnapshot.provider_name == "Last.fm",
        MetricSnapshot.market_code == code,
        MetricSnapshot.metric_code == "lastfm_chart_rank",
    )
    if platform:
        query = query.where(MetricSnapshot.platform == platform.casefold())
    rows = session.scalars(query.order_by(MetricSnapshot.observed_at.desc()).limit(min(max(limit, 1), 500) * 8)).all()
    latest: dict[tuple[str, str], MetricSnapshot] = {}
    for row in rows:
        latest.setdefault((row.provider_entity_id, row.platform), row)
    ordered = sorted(latest.values(), key=lambda row: (row.platform, row.value, row.entity_label or ""))
    return [
        {
            "entity_id": row.provider_entity_id,
            "title": row.entity_label or "Título no informado por el proveedor",
            "artist": row.artist_label,
            "platform": row.platform,
            "market_code": row.market_code,
            "position": row.value,
            "observed_at": _timestamp_text(row.observed_at),
            "captured_at": _timestamp_text(row.captured_at),
            "confidence": row.confidence,
            "source": row.provider_name,
        }
        for row in ordered[:min(max(limit, 1), 500)]
    ]


@app.get("/api/charts/tracks", tags=["charts"])
def lastfm_chart_tracks(
    market_code: str = "GLOBAL",
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    """Return the latest captured Last.fm chart and the prior rank snapshot."""
    market = market_code.strip().upper()
    if market != "GLOBAL" and (len(market) != 2 or not market.isalpha()):
        raise HTTPException(status_code=422, detail="market_code must be GLOBAL or an ISO alpha-2 code")
    bounded_limit = min(max(limit, 1), 100)
    latest_capture = session.scalar(
        select(func.max(MetricSnapshot.captured_at)).where(
            MetricSnapshot.provider_name == "Last.fm",
            MetricSnapshot.market_code == market,
            MetricSnapshot.metric_code == "lastfm_chart_rank",
        )
    )
    if latest_capture is None:
        return {
            "source": "Last.fm",
            "market_code": market,
            "captured_at": None,
            "chart_window": "not_available",
            "playcount_label": "not_available",
            "items": [],
        }
    rows = session.scalars(
        select(MetricSnapshot).where(
            MetricSnapshot.provider_name == "Last.fm",
            MetricSnapshot.market_code == market,
            MetricSnapshot.captured_at == latest_capture,
            MetricSnapshot.metric_code.in_(("lastfm_chart_rank", "lastfm_chart_playcount")),
        )
    ).all()
    grouped: dict[str, dict[str, MetricSnapshot]] = {}
    for row in rows:
        grouped.setdefault(row.provider_entity_id, {})[row.metric_code] = row
    ranked = sorted(
        (values for values in grouped.values() if "lastfm_chart_rank" in values),
        key=lambda values: (values["lastfm_chart_rank"].value, values["lastfm_chart_rank"].entity_label or ""),
    )[:bounded_limit]
    entity_ids = [values["lastfm_chart_rank"].provider_entity_id for values in ranked]
    previous_rows = []
    if entity_ids:
        previous_rows = session.scalars(
            select(MetricSnapshot).where(
                MetricSnapshot.provider_name == "Last.fm",
                MetricSnapshot.market_code == market,
                MetricSnapshot.metric_code == "lastfm_chart_rank",
                MetricSnapshot.provider_entity_id.in_(entity_ids),
                MetricSnapshot.captured_at < latest_capture,
            ).order_by(MetricSnapshot.captured_at.desc())
        ).all()
    previous_by_id: dict[str, MetricSnapshot] = {}
    for row in previous_rows:
        previous_by_id.setdefault(row.provider_entity_id, row)
    regional = market != "GLOBAL"
    return {
        "source": "Last.fm",
        "market_code": market,
        "captured_at": _timestamp_text(latest_capture),
        "chart_window": "last_week" if regional else "not_documented_by_provider",
        "playcount_label": "Last.fm reproducciones · última semana" if regional else "Last.fm reproducciones · período no especificado",
        "rank_change_label": "posiciones vs captura previa (positivo = subió)",
        "items": [
            {
                "entity_id": values["lastfm_chart_rank"].provider_entity_id,
                "title": values["lastfm_chart_rank"].entity_label,
                "artist": values["lastfm_chart_rank"].artist_label,
                "position": values["lastfm_chart_rank"].value,
                "playcount": values.get("lastfm_chart_playcount").value if values.get("lastfm_chart_playcount") else None,
                "rank_change": (
                    previous_by_id[values["lastfm_chart_rank"].provider_entity_id].value
                    - values["lastfm_chart_rank"].value
                    if values["lastfm_chart_rank"].provider_entity_id in previous_by_id else None
                ),
                "previous_captured_at": (
                    _timestamp_text(previous_by_id[values["lastfm_chart_rank"].provider_entity_id].captured_at)
                    if values["lastfm_chart_rank"].provider_entity_id in previous_by_id else None
                ),
                "captured_at": _timestamp_text(values["lastfm_chart_rank"].captured_at),
                "lastfm_url": values["lastfm_chart_rank"].source_url,
            }
            for values in ranked
        ],
    }


@app.get("/api/metrics/snapshots", tags=["metrics"])
def provider_metric_snapshots(
    provider: str,
    market_code: str = "GLOBAL",
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    """Return latest source-attributed observations and their prior local capture."""
    if provider not in {"Soundcharts", "Chartmetric", "Last.fm"}:
        raise HTTPException(status_code=422, detail="provider must be Soundcharts, Chartmetric, or Last.fm")
    market = market_code.strip().upper()
    if market != "GLOBAL" and (len(market) != 2 or not market.isalpha()):
        raise HTTPException(status_code=422, detail="market_code must be GLOBAL or an ISO alpha-2 code")
    bounded_limit = min(max(limit, 1), 500)
    rows = session.scalars(
        select(MetricSnapshot).where(
            MetricSnapshot.provider_name == provider,
            MetricSnapshot.market_code == market,
        ).order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.observed_at.desc()).limit(10000)
    ).all()
    grouped: dict[tuple[str, str, str, str], list[MetricSnapshot]] = {}
    for row in rows:
        key = (row.entity_type, row.provider_entity_id, row.metric_code, row.platform)
        grouped.setdefault(key, []).append(row)
    latest_items = []
    for (_, entity_id, metric_code, platform), samples in grouped.items():
        samples.sort(key=lambda item: (item.captured_at, item.observed_at), reverse=True)
        latest = samples[0]
        previous = next((sample for sample in samples[1:] if sample.captured_at < latest.captured_at), None)
        latest_items.append({
            "entity_id": entity_id,
            "title": latest.entity_label or "Título no informado por el proveedor",
            "artist": latest.artist_label,
            "metric_code": metric_code,
            "platform": platform,
            "market_code": market,
            "value": latest.value,
            "unit": latest.unit,
            "change": latest.value - previous.value if previous is not None else None,
            "previous_value": previous.value if previous is not None else None,
            "observed_at": _timestamp_text(latest.observed_at),
            "captured_at": _timestamp_text(latest.captured_at),
            "previous_captured_at": _timestamp_text(previous.captured_at) if previous else None,
            "confidence": latest.confidence,
            "coverage": latest.coverage,
            "source_url": latest.source_url,
        })
    latest_items.sort(key=lambda item: (item["captured_at"], item["metric_code"], item["title"]), reverse=True)
    return {
        "source": provider,
        "market_code": market,
        "item_count": min(len(latest_items), bounded_limit),
        "captured_at": latest_items[0]["captured_at"] if latest_items else None,
        "items": latest_items[:bounded_limit],
    }


@app.get("/api/radar/tracks", tags=["intelligence"])
def radar_tracks(
    market_code: str = "GLOBAL",
    score_type: str = "trend",
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[dict]:
    if score_type not in {"trend", "breakout", "elite", "next", "global_reach", "export_potential", "authenticity"}:
        raise HTTPException(status_code=422, detail="unsupported score_type")
    latest_rows = _latest_score_rows()
    query = (
        select(IntelligenceScore)
        .join(latest_rows, latest_rows.c.score_id == IntelligenceScore.id)
        .where(
            latest_rows.c.row_number == 1,
            IntelligenceScore.score_type == score_type,
            IntelligenceScore.market_code == market_code.upper(),
            IntelligenceScore.score_value.is_not(None),
        )
        .order_by(IntelligenceScore.score_value.desc())
        .limit(min(max(limit, 1), 500))
    )
    rows = session.scalars(query).all()
    return [
        {
            "entity_id": row.provider_entity_id,
            "title": row.entity_label or "Título no informado por el proveedor",
            "artist": row.artist_label,
            "market_code": row.market_code,
            "score_type": row.score_type,
            "score": row.score_value,
            "confidence": row.confidence,
            "evidence_coverage": row.evidence_coverage,
            "status": row.status,
            "components": row.components,
            "evidence": row.evidence,
            "algorithm_version": row.algorithm_version,
            "calculated_at": _timestamp_text(row.calculated_at),
        }
        for row in rows
    ]


@app.get("/api/intelligence/propagation", tags=["intelligence"])
def propagation_routes(
    destination_market: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    query = select(CrossBorderPropagation).where(
        CrossBorderPropagation.evidence["source_provider"].as_string().in_(["Last.fm", "Chartmetric"])
    ).order_by(CrossBorderPropagation.first_detected_at.desc())
    if destination_market:
        query = query.where(CrossBorderPropagation.destination_market == destination_market.upper())
    rows = session.scalars(query.limit(min(max(limit, 1), 500))).all()
    return {
        "interpretation": "Describe orden temporal de primera detección en charts; no demuestra causalidad ni migración de oyentes.",
        "items": [
            {
                "entity_id": row.provider_entity_id,
                "origin_market": row.origin_market,
                "destination_market": row.destination_market,
                "first_detected_at": _timestamp_text(row.first_detected_at),
                "days_between_first_observations": row.days_to_propagate,
                "confidence": row.confidence,
                "evidence": row.evidence,
            }
            for row in rows
        ],
    }


@app.get("/api/intelligence/signal-order", tags=["intelligence"])
def intelligence_signal_order(
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    """Order source observations for review before any playlist publication step."""
    statuses = _provider_statuses(session)
    return build_signal_order(session, statuses, limit=limit)


@app.get("/api/intelligence/provider-comparison", tags=["intelligence"])
def intelligence_provider_comparison(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict:
    """Review Soundcharts and Chartmetric observations side by side before playlists."""
    return build_provider_comparison(session, _provider_statuses(session), limit=limit)


@app.get("/api/public/playlists", tags=["playlists"])
def public_playlists(session: Session = Depends(get_session)) -> dict:
    """Show the existing public playlist network and its configured editorial DNA."""
    return public_playlist_registry_view(_provider_statuses(session))


@app.get("/api/playlist-blueprints", tags=["playlists"])
def playlist_blueprints(session: Session = Depends(get_session)) -> dict:
    """Return measured launch readiness without creating empty playlists."""
    statuses = _provider_statuses(session)
    configs = load_playlists()
    plans = build_blueprint_launch_plans(session, statuses, existing_configs=configs)
    return playlist_blueprint_registry_view(
        statuses,
        existing_playlist_count=len(configs),
        catalog_track_count=session.scalar(select(func.count()).select_from(Track)) or 0,
        catalog_artist_count=session.scalar(select(func.count()).select_from(Artist)) or 0,
        launch_plans=plans,
    )


@app.get("/api/campaigns", tags=["campaigns"])
def organic_campaigns(session: Session = Depends(get_session)) -> dict:
    """Return manual organic campaign plans; this route never publishes content."""
    return campaign_registry_view(_provider_statuses(session), session)


@app.post("/api/campaigns/{playlist_slug}/report", tags=["campaigns"])
def save_organic_campaign_report(
    playlist_slug: str,
    payload: OrganicCampaignReportInput,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Save manually observed organic campaign results for a public playlist."""
    _require_local_same_origin(request, require_origin=True)
    config = next((item for item in load_playlists() if item.slug == playlist_slug and item.public), None)
    if config is None:
        raise HTTPException(status_code=404, detail="Public playlist campaign was not found.")
    if payload.period_end < payload.period_start:
        raise HTTPException(status_code=422, detail="El cierre del periodo debe ser igual o posterior al inicio.")
    if (payload.period_end - payload.period_start).days > 90:
        raise HTTPException(status_code=422, detail="El periodo de campaña no puede superar 90 días.")
    followers_pair = payload.followers_start is not None and payload.followers_end is not None
    if (payload.followers_start is None) != (payload.followers_end is None):
        raise HTTPException(status_code=422, detail="Registra seguidores al inicio y al cierre, o deja ambos vacíos.")
    if followers_pair and not (payload.followers_source or "").strip():
        raise HTTPException(status_code=422, detail="Indica de dónde leíste los seguidores de la playlist.")
    if not any((payload.views is not None, payload.reach is not None, payload.link_clicks is not None, followers_pair)):
        raise HTTPException(status_code=422, detail="Registra al menos una métrica observada para guardar el periodo.")
    channel = payload.channel.strip()
    source_name = payload.source_name.strip()
    if not channel or not source_name:
        raise HTTPException(status_code=422, detail="Completa el canal y la fuente de medición.")

    report = session.scalar(
        select(OrganicCampaignReport).where(
            OrganicCampaignReport.playlist_slug == playlist_slug,
            OrganicCampaignReport.period_start == payload.period_start,
            OrganicCampaignReport.period_end == payload.period_end,
        )
    )
    if report is None:
        report = OrganicCampaignReport(
            playlist_slug=playlist_slug,
            period_start=payload.period_start,
            period_end=payload.period_end,
            channel=channel,
            source_name=source_name,
        )
        session.add(report)
    report.channel = channel
    report.source_name = source_name
    report.followers_source = (payload.followers_source or "").strip() or None
    report.views = payload.views
    report.reach = payload.reach
    report.link_clicks = payload.link_clicks
    report.followers_start = payload.followers_start
    report.followers_end = payload.followers_end
    report.notes = (payload.notes or "").strip() or None
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="No se pudo guardar el reporte local.") from exc
    return {"saved": True, "report": campaign_report_view(report)}


@app.post("/api/public/playlists/generate", tags=["playlists"])
def generate_public_playlists(
    playlist_slug: str | None = None,
    limit: int = 25,
    session: Session = Depends(get_session),
) -> dict:
    """Build review-only playlist change drafts; this endpoint never writes to Spotify."""
    try:
        return generate_public_playlist_drafts(
            session,
            _provider_statuses(session),
            playlist_slug=playlist_slug,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/public/playlists/{playlist_slug}/refresh", tags=["playlists"])
def refresh_public_playlist_draft(
    playlist_slug: str,
    limit: int = 25,
    session: Session = Depends(get_session),
) -> dict:
    """Refresh one playlist's local review draft; Spotify playlist contents are not changed."""
    try:
        return generate_public_playlist_drafts(
            session,
            _provider_statuses(session),
            playlist_slug=playlist_slug,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/public/playlists/{playlist_slug}/lastfm-review", tags=["playlists"])
def review_lastfm_playlist_candidates(
    playlist_slug: str,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Build a local review from current Soundcharts and Last.fm chart signals with exact Spotify catalog matches."""
    remote_host = request.client.host if request.client else ""
    try:
        local_request = ipaddress.ip_address(remote_host).is_loopback
    except ValueError:
        local_request = False
    if not local_request:
        raise HTTPException(status_code=403, detail="This catalog review route is available only from the local machine.")
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin.rstrip("/") != expected_origin:
        raise HTTPException(status_code=403, detail="Cross-origin catalog review requests are not accepted.")
    try:
        playlist = get_playlist_config(playlist_slug)
    except PlaylistConfigError:
        raise HTTPException(status_code=404, detail="Playlist configuration was not found.") from None

    try:
        settings = load_settings()
    except ValueError:
        raise HTTPException(status_code=503, detail="Spotify catalog search is not configured on this local server.") from None
    token_store = TokenStore(settings.client_id)
    if token_store.load() is None:
        raise HTTPException(status_code=409, detail="Connect Spotify from this local app before matching catalog tracks.")

    lastfm = LastFmProvider.from_environment()
    lastfm.set_request_budget(MAX_REVIEW_CANDIDATES)
    if not lastfm.credentials_configured:
        lastfm.close()
        raise HTTPException(status_code=503, detail="Last.fm API key is not configured.")
    if not lastfm.scope_confirmed:
        lastfm.close()
        raise HTTPException(status_code=403, detail="Last.fm non-commercial and outside-EEA use scope is not confirmed.")

    try:
        auth = OAuthManager(settings, token_store=token_store, timeout=settings.request_timeout)
        with SpotifyClient(settings, auth=auth) as spotify:
            return generate_lastfm_playlist_review(session, playlist, lastfm, spotify)
    except SpotifyAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Spotify catalog search failed (HTTP {exc.status_code or 'network'}). No results were saved.") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    finally:
        lastfm.close()
