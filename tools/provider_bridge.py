#!/usr/bin/env python3
"""Fetch permitted provider metrics out of process and push normalized rows to WORLD MUSIC OS."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.world_music.ingestion import (  # noqa: E402
    _chart_metric_records,
    _growth_metric_records,
    CHART_PLATFORMS,
)
from app.world_music.lastfm_ingestion import _snapshots as lastfm_snapshots  # noqa: E402
from app.world_music.models import now_utc  # noqa: E402
from app.world_music.free_access import (  # noqa: E402
    chartmetric_free_trial_state,
    soundcharts_free_trial_enabled,
)
from app.world_music.provider_ingest import (  # noqa: E402
    MAX_OBSERVATIONS,
    MAX_REQUESTS,
    PROVIDER_NAMES,
    ingest_api_token,
    provider_rights_enabled,
    settings,
)
from app.world_music.providers.chartmetric import (  # noqa: E402
    GROWTH_METRICS,
    ChartmetricAPIError,
    ChartmetricFreeTrialRequired,
    ChartmetricProvider,
)
from app.world_music.providers.lastfm import (  # noqa: E402
    COUNTRY_NAMES,
    LastFmAPIError,
    LastFmProvider,
)
from app.world_music.providers.soundcharts import (  # noqa: E402
    SoundchartsAPIError,
    SoundchartsProvider,
)
from app.world_music.soundcharts_ingestion import normalize_soundcharts_chart  # noqa: E402


class BridgeError(RuntimeError):
    pass


def _env_list(name: str, default: str = "") -> list[str]:
    value = settings().get(name, default)
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _safe_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9_:;.-]+", "_", str(value).upper())[:80].strip("_") or "UPSTREAM_ERROR"


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "provider_entity_id": snapshot.provider_entity_id,
        "entity_type": snapshot.entity_type,
        "entity_label": snapshot.entity_label,
        "artist_label": snapshot.artist_label,
        "release_date": snapshot.release_date.isoformat() if snapshot.release_date else None,
        "metric_code": snapshot.metric_code,
        "platform": snapshot.platform,
        "market_code": snapshot.market_code,
        "value": snapshot.value,
        "unit": snapshot.unit,
        "observed_at": snapshot.observed_at.astimezone(timezone.utc).isoformat(),
        "confidence": snapshot.confidence,
        "coverage": snapshot.coverage,
        "source_url": snapshot.source_url,
    }


def _batch(
    provider_name: str,
    observations: list[Any],
    *,
    request_count: int,
    markets: list[str],
    rate_limit_status: str,
    errors: list[str],
    successful_calls: int,
    truncated: bool = False,
) -> dict[str, Any]:
    if truncated:
        errors.append("OBSERVATION_LIMIT_REACHED")
    status = "FAILED" if errors and successful_calls == 0 else "PARTIAL" if errors else "SUCCEEDED"
    return {
        "provider_name": provider_name,
        "status": status,
        "request_count": min(request_count, MAX_REQUESTS),
        "markets_requested": list(dict.fromkeys(markets)),
        "rate_limit_status": _safe_code(rate_limit_status),
        "error_code": ";".join(dict.fromkeys(_safe_code(error) for error in errors))[:80] or None,
        "observations": [_snapshot_payload(row) for row in observations[:MAX_OBSERVATIONS]],
    }


def _add_observations(target: list[Any], rows: list[Any]) -> tuple[bool, bool]:
    remaining = MAX_OBSERVATIONS - len(target)
    if remaining <= 0:
        return False, bool(rows)
    target.extend(rows[:remaining])
    return True, len(rows) > remaining


def _lastfm_batch(*, limit: int, max_requests: int, markets: list[str]) -> dict[str, Any]:
    provider = LastFmProvider.from_environment()
    if provider is None:
        raise BridgeError("LASTFM_API_KEY is not configured")
    if not provider.scope_confirmed:
        raise BridgeError("Last.fm requires the non-commercial and non-EEA terms gates before data access")
    unsupported = sorted(set(markets) - {"GLOBAL", *COUNTRY_NAMES})
    if unsupported:
        raise BridgeError("LASTFM_MARKETS contains unsupported market codes")
    markets = ["GLOBAL", *[market for market in markets if market != "GLOBAL"]]
    if len(markets) > max_requests:
        raise BridgeError("max requests must cover each configured Last.fm market")
    provider.set_request_budget(max_requests)
    captured_at = now_utc()
    observations: list[Any] = []
    errors: list[str] = []
    successful_calls = 0
    queried: list[str] = []
    truncated = False
    try:
        for market in markets:
            try:
                response = provider.fetch_top_tracks(market, limit=limit)
            except LastFmAPIError as exc:
                errors.append(f"{market}_{exc.code}")
                if exc.code in {"401", "429", "10", "29", "LOCAL_REQUEST_BUDGET_REACHED"}:
                    break
                continue
            successful_calls += 1
            queried.append(market)
            rows = lastfm_snapshots(response, market=market, captured_at=captured_at, run_id="external-bridge")
            _, truncated = _add_observations(observations, rows)
            if truncated:
                break
    finally:
        request_count = provider.requests_made
        provider.close()
    return _batch(
        "Last.fm", observations, request_count=request_count, markets=queried,
        rate_limit_status="NOT_REPORTED_BY_PROVIDER", errors=errors,
        successful_calls=successful_calls, truncated=truncated,
    )


def _chartmetric_batch(*, limit: int, max_requests: int, markets: list[str]) -> dict[str, Any]:
    provider = ChartmetricProvider.from_environment()
    if provider is None:
        raise BridgeError("CHARTMETRIC_REFRESH_TOKEN is not configured")
    if not provider.rights_confirmed:
        raise BridgeError("Chartmetric written rights gate is not enabled")
    if chartmetric_free_trial_state() != "FREE_TRIAL_ACTIVE":
        raise BridgeError("Chartmetric API calls are limited to the manually activated free 7-day trial window")
    markets = list(dict.fromkeys(["GLOBAL", *[market.upper() for market in markets]]))
    if any(market != "GLOBAL" and not re.fullmatch(r"[A-Z]{2}", market) for market in markets):
        raise BridgeError("CHARTMETRIC_MARKETS must contain ISO alpha-2 codes")
    provider.set_request_budget(max_requests)
    captured_at = now_utc()
    observations: list[Any] = []
    errors: list[str] = []
    successful_calls = 0
    queried: list[str] = []
    rate_status = "NOT_REPORTED"
    truncated = False
    try:
        for stat, provider_metric in GROWTH_METRICS.items():
            if provider.requests_made >= max_requests:
                errors.append("REQUEST_BUDGET_REACHED")
                break
            try:
                response = provider.fetch_growth_tracks(stat, limit=limit)
            except ChartmetricAPIError as exc:
                errors.append(f"GROWTH_{stat}_{exc.status_code}_{exc.code}")
                if exc.status_code in {401, 429}:
                    break
                continue
            successful_calls += 1
            rate_status = f"RATE_LIMIT_REMAINING:{response.rate_limit}" if response.rate_limit else "RESPONSE_OK_LIMIT_UNKNOWN"
            rows = _growth_metric_records(response, metric=stat, provider_stat=provider_metric, captured_at=captured_at, sync_run_id="external-bridge")
            _, truncated = _add_observations(observations, rows)
            if truncated:
                break

        if not truncated and (not errors or "REQUEST_BUDGET_REACHED" not in errors):
            should_stop = False
            for market in markets:
                queried.append(market)
                for platform in CHART_PLATFORMS:
                    if provider.requests_made >= max_requests:
                        errors.append("REQUEST_BUDGET_REACHED")
                        should_stop = True
                        break
                    try:
                        response = provider.fetch_chart(platform, market, limit=limit)
                    except ChartmetricAPIError as exc:
                        errors.append(f"{market}_{platform}_{exc.status_code}_{exc.code}")
                        if exc.status_code in {401, 429}:
                            should_stop = True
                            break
                        continue
                    successful_calls += 1
                    rate_status = f"RATE_LIMIT_REMAINING:{response.rate_limit}" if response.rate_limit else "RESPONSE_OK_LIMIT_UNKNOWN"
                    rows = _chart_metric_records(response, platform=platform, market=market, captured_at=captured_at, sync_run_id="external-bridge")
                    _, truncated = _add_observations(observations, rows)
                    if truncated:
                        should_stop = True
                        break
                if should_stop:
                    break
    finally:
        request_count = provider.requests_made
        provider.close()
    return _batch(
        "Chartmetric", observations, request_count=request_count, markets=queried,
        rate_limit_status=rate_status, errors=errors,
        successful_calls=successful_calls, truncated=truncated,
    )


def _soundcharts_batch(
    *, limit: int, max_requests: int, chart_slugs: list[str], trial_remaining: int
) -> dict[str, Any]:
    provider = SoundchartsProvider.from_environment()
    if provider is None:
        raise BridgeError("Soundcharts API credentials are not configured")
    if not provider.rights_confirmed:
        raise BridgeError("Soundcharts written rights gate is not enabled")
    if not soundcharts_free_trial_enabled():
        raise BridgeError("Soundcharts API calls are limited to the explicitly enabled free 1,000-request trial")
    if not chart_slugs:
        raise BridgeError("Set SOUNDCHARTS_CHART_SLUGS to the exact charts to query")
    effective_budget = min(max_requests, max(trial_remaining, 0), 100)
    if effective_budget <= 0:
        raise BridgeError("Soundcharts local free-trial request budget is exhausted")
    if len(chart_slugs) > effective_budget:
        raise BridgeError("max requests and remaining free-trial budget must cover every selected chart slug")
    provider.set_request_budget(effective_budget)
    captured_at = now_utc()
    observations: list[Any] = []
    errors: list[str] = []
    successful_calls = 0
    queried: list[str] = []
    markets_seen: set[str] = set()
    truncated = False
    try:
        for slug in chart_slugs:
            try:
                payload = provider.fetch_latest_song_chart(slug, limit=limit)
            except SoundchartsAPIError as exc:
                errors.append(f"{slug}_{exc.status_code}_{exc.code}")
                if exc.status_code in {401, 403, 429}:
                    break
                continue
            successful_calls += 1
            queried.append(slug)
            rows, market = normalize_soundcharts_chart(payload, requested_slug=slug, captured_at=captured_at, run_id="external-bridge")
            markets_seen.add(market)
            _, truncated = _add_observations(observations, rows)
            if truncated:
                break
    finally:
        request_count = provider.requests_made
        quota_remaining = provider.quota_remaining
        provider.close()
    rate_status = f"QUOTA_REMAINING:{quota_remaining}" if quota_remaining else "QUOTA_NOT_REPORTED"
    return _batch(
        "Soundcharts", observations, request_count=request_count,
        markets=sorted(markets_seen),
        rate_limit_status=rate_status, errors=errors,
        successful_calls=successful_calls, truncated=truncated,
    )


def _validate_api_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BridgeError("The ingest API URL must be a valid HTTPS or loopback HTTP URL")
    if parsed.query or parsed.fragment:
        raise BridgeError("The ingest API URL must not contain a query or fragment")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {"127.0.0.1", "localhost", "::1"}:
        raise BridgeError("Plain HTTP is allowed only for a loopback API URL")
    return url.rstrip("/")


def _receiver_soundcharts_budget() -> int:
    token = ingest_api_token()
    if len(token) < 32:
        raise BridgeError("WORLD_MUSIC_INGEST_API_TOKEN must contain at least 32 characters")
    api_url = _validate_api_url(settings().get("WORLD_MUSIC_INGEST_API_URL", "http://127.0.0.1:8002"))
    try:
        response = httpx.get(
            f"{api_url}/api/providers/free-budget",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
    except httpx.RequestError as exc:
        raise BridgeError(f"No se pudo consultar el presupuesto local ({type(exc).__name__}); no se llamó a Soundcharts.") from None
    if response.status_code >= 400:
        raise BridgeError(f"La API local rechazó la consulta de presupuesto (HTTP {response.status_code}); no se llamó a Soundcharts.")
    try:
        budget = response.json().get("soundcharts", {})
        if not budget.get("access_mode_active"):
            raise BridgeError("Soundcharts free-trial mode is not active in the receiving app")
        remaining = int(budget.get("local_requests_remaining", 0))
    except (AttributeError, TypeError, ValueError):
        raise BridgeError("La API local respondió un presupuesto de prueba no válido; no se llamó a Soundcharts.") from None
    return max(remaining, 0)


def _push(batch: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        print(f"{batch['provider_name']}: preparado ({len(batch['observations'])} observaciones; {batch['request_count']} solicitudes; estado {batch['status']}); no enviado")
        return
    token = ingest_api_token()
    if len(token) < 32:
        raise BridgeError("WORLD_MUSIC_INGEST_API_TOKEN must contain at least 32 characters")
    api_url = _validate_api_url(settings().get("WORLD_MUSIC_INGEST_API_URL", "http://127.0.0.1:8002"))
    try:
        response = httpx.post(
            f"{api_url}/api/providers/ingest",
            json=batch,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
    except httpx.RequestError as exc:
        raise BridgeError(f"No se pudo contactar la API local ({type(exc).__name__}).") from None
    if response.status_code >= 400:
        raise BridgeError(f"La API rechazó el lote (HTTP {response.status_code}); revisa los permisos/configuración del servidor.")
    try:
        result = response.json()
    except ValueError:
        raise BridgeError("La API respondió con un resultado ilegible.") from None
    print(
        f"{batch['provider_name']}: {result.get('status')} — "
        f"{result.get('observations_written')} nuevas, {result.get('duplicates_skipped')} duplicadas; "
        f"lote {result.get('run_id')}"
    )


def _status() -> int:
    values = settings()
    checks = {
        "Soundcharts": (
            bool(values.get("SOUNDCHARTS_CLIENT_ID", "").strip() and values.get("SOUNDCHARTS_CLIENT_SECRET", "").strip())
            or bool(values.get("SOUNDCHARTS_APP_ID", "").strip() and values.get("SOUNDCHARTS_API_KEY", "").strip()),
            provider_rights_enabled("Soundcharts"),
            soundcharts_free_trial_enabled(),
            bool(_env_list("SOUNDCHARTS_CHART_SLUGS")),
        ),
        "Chartmetric": (
            bool(values.get("CHARTMETRIC_REFRESH_TOKEN", "").strip()),
            provider_rights_enabled("Chartmetric"),
            chartmetric_free_trial_state() == "FREE_TRIAL_ACTIVE",
            True,
        ),
        "Last.fm": (
            bool(values.get("LASTFM_API_KEY", "").strip()),
            provider_rights_enabled("Last.fm"),
            True,
            True,
        ),
    }
    print("WORLD MUSIC OS external provider bridge — configuración local (sin llamadas a proveedores)")
    for name, (credentials, rights, free_access, selectors) in checks.items():
        missing = []
        if not credentials:
            missing.append("credenciales API")
        if not rights:
            missing.append("derechos/alcance confirmado")
        if not free_access:
            missing.append("modo de acceso gratuito activo")
        if not selectors:
            missing.append("SOUNDCHARTS_CHART_SLUGS")
        print(f"- {name}: " + ("LISTO" if not missing else "BLOQUEADO — " + ", ".join(missing)))
    print("- Soundcharts: límite local de 1.000 solicitudes de prueba; no hay modo pagado habilitado")
    print(f"- Chartmetric: estado de prueba gratuita = {chartmetric_free_trial_state()}; dura como máximo 7 días")
    print("- Last.fm: API gratuita sujeta a uso no comercial y requisitos territoriales/atribución")
    if len(ingest_api_token()) < 32:
        print("- API receptora: falta WORLD_MUSIC_INGEST_API_TOKEN de al menos 32 caracteres")
    else:
        print("- API receptora: token local configurado (valor oculto)")
    print(f"- Destino: {values.get('WORLD_MUSIC_INGEST_API_URL', 'http://127.0.0.1:8002')}")
    return 0 if any(credentials and rights and free_access and selectors for credentials, rights, free_access, selectors in checks.values()) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="External, rights-gated metrics bridge to WORLD MUSIC OS")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show local readiness without contacting providers")
    push = commands.add_parser("push", help="Fetch authorized API metrics and submit normalized observations")
    push.add_argument("--providers", default="soundcharts,chartmetric,lastfm", help="Comma-separated provider names")
    push.add_argument("--markets", default=None, help="Comma-separated ISO market codes; defaults to each provider's .env setting")
    push.add_argument("--chart-slugs", default=None, help="Explicit Soundcharts chart slugs; defaults to SOUNDCHARTS_CHART_SLUGS")
    values = settings()
    try:
        default_limit = int(values.get("WORLD_MUSIC_BRIDGE_LIMIT", "100"))
        default_requests = int(values.get("WORLD_MUSIC_BRIDGE_MAX_REQUESTS", "40"))
    except ValueError:
        default_limit, default_requests = 100, 40
    push.add_argument("--limit", type=int, default=default_limit, help="Maximum records per source request (1–100)")
    push.add_argument("--max-requests", type=int, default=default_requests, help="Per-provider request budget (1–100)")
    push.add_argument("--dry-run", action="store_true", help="Fetch and normalize metrics, but do not send them to the app")
    args = parser.parse_args()
    if args.command == "status":
        return _status()
    if not 1 <= args.limit <= 100 or not 1 <= args.max_requests <= MAX_REQUESTS:
        parser.error("--limit must be 1–100 and --max-requests must be 1–100")

    selected = [item.strip().casefold() for item in args.providers.split(",") if item.strip()]
    canonical = {name.casefold(): name for name in PROVIDER_NAMES}
    if not selected or any(name not in canonical for name in selected):
        parser.error("--providers accepts soundcharts, chartmetric, and lastfm")
    markets_override = [item.strip().upper() for item in args.markets.split(",") if item.strip()] if args.markets is not None else None
    chart_slugs = [item.strip() for item in args.chart_slugs.split(",") if item.strip()] if args.chart_slugs is not None else _env_list("SOUNDCHARTS_CHART_SLUGS")
    failures = 0
    for selected_name in selected:
        name = canonical[selected_name]
        try:
            if name == "Last.fm":
                markets = markets_override if markets_override is not None else [item.upper() for item in _env_list("LASTFM_MARKETS", "CL,MX,AR,CO,ES,US,BR")]
                batch = _lastfm_batch(limit=args.limit, max_requests=args.max_requests, markets=markets)
            elif name == "Chartmetric":
                markets = markets_override if markets_override is not None else [item.upper() for item in _env_list("CHARTMETRIC_MARKETS", "CL,MX,AR,CO,ES,US,BR")]
                batch = _chartmetric_batch(limit=args.limit, max_requests=args.max_requests, markets=markets)
            else:
                trial_remaining = _receiver_soundcharts_budget()
                batch = _soundcharts_batch(
                    limit=args.limit,
                    max_requests=args.max_requests,
                    chart_slugs=chart_slugs,
                    trial_remaining=trial_remaining,
                )
            _push(batch, dry_run=args.dry_run)
        except BridgeError as exc:
            failures += 1
            print(f"{name}: BLOQUEADO — {exc}")
        except Exception as exc:
            failures += 1
            print(f"{name}: ERROR — {type(exc).__name__}; no se muestra el contenido de la respuesta ni credenciales")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
