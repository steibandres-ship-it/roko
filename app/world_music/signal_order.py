from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MetricSnapshot
from .providers.base import ProviderStatus


ORDERING_VERSION = "signal-order-v1"
MAX_SCAN_ROWS = 10_000
ALLOWED_RIGHTS_BASIS = {
    "Soundcharts": {"licensed"},
    "Chartmetric": {"licensed"},
    "Last.fm": {"provider_terms"},
}
USABLE_PROVIDER_STATES = {"CONNECTED", "DEGRADED"}


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _comparator(metric_code: str, unit: str) -> str:
    code = metric_code.casefold()
    normalized_unit = unit.casefold()
    if "rank" in code or "position" in code or normalized_unit in {"rank", "chart_rank"}:
        return "lower_is_stronger"
    if code.startswith("weekly_growth_percent_") or "growth" in code or "percent" in normalized_unit:
        return "higher_is_stronger"
    if code == "lastfm_chart_playcount" or any(token in normalized_unit for token in ("count", "stream", "view", "play", "spin")):
        return "higher_is_stronger"
    return "newest_only"


def _allowed_status(status: ProviderStatus | None) -> bool:
    return bool(status and status.state in USABLE_PROVIDER_STATES)


def build_signal_order(
    session: Session,
    provider_statuses: list[ProviderStatus],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Create an auditable review queue without blending incomparable metrics or publishing to Spotify."""
    bounded_limit = min(max(limit, 1), 500)
    provider_by_name = {status.provider_name: status for status in provider_statuses}

    rows = session.scalars(
        select(MetricSnapshot)
        .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.observed_at.desc())
        .limit(MAX_SCAN_ROWS + 1)
    ).all()
    truncated = len(rows) > MAX_SCAN_ROWS
    rows = rows[:MAX_SCAN_ROWS]

    latest_by_key: dict[tuple[str, str, str, str, str, str], MetricSnapshot] = {}
    previous_by_key: dict[tuple[str, str, str, str, str, str], MetricSnapshot] = {}
    for row in rows:
        key = (
            row.provider_name,
            row.entity_type,
            row.provider_entity_id,
            row.metric_code,
            row.platform,
            row.market_code,
        )
        if key not in latest_by_key:
            latest_by_key[key] = row
        elif key not in previous_by_key:
            previous_by_key[key] = row

    counts = {
        "latest_signals_found": len(latest_by_key),
        "eligible_for_review": 0,
        "blocked_by_provider": 0,
        "blocked_by_rights": 0,
        "blocked_by_measurement": 0,
        "spotify_catalog_match_pending": 0,
    }
    eligible: list[MetricSnapshot] = []
    blocked: list[dict[str, str]] = []

    for row in latest_by_key.values():
        status = provider_by_name.get(row.provider_name)
        if not _allowed_status(status):
            counts["blocked_by_provider"] += 1
            blocked.append({"provider": row.provider_name, "reason": "provider_not_connected_or_verified"})
            continue
        if row.rights_basis not in ALLOWED_RIGHTS_BASIS.get(row.provider_name, set()):
            counts["blocked_by_rights"] += 1
            blocked.append({"provider": row.provider_name, "reason": "rights_basis_not_allowed_for_provider"})
            continue
        if (
            row.entity_type != "track"
            or not row.metric_code
            or not row.unit
            or not math.isfinite(float(row.value))
            or row.observed_at is None
        ):
            counts["blocked_by_measurement"] += 1
            blocked.append({"provider": row.provider_name, "reason": "required_measurement_fields_missing_or_invalid"})
            continue
        eligible.append(row)
        if not row.canonical_entity_id:
            counts["spotify_catalog_match_pending"] += 1

    counts["eligible_for_review"] = len(eligible)

    grouped: dict[tuple[str, str, str, str, str, str, str], list[MetricSnapshot]] = defaultdict(list)
    for row in eligible:
        # A provider sync run (or exact capture timestamp when no run is recorded) defines
        # a comparable snapshot window. Different providers and metrics never share a cohort.
        capture_key = row.sync_run_id or _timestamp_text(row.captured_at) or "unknown-capture"
        key = (
            row.provider_name,
            row.metric_code,
            row.platform,
            row.market_code,
            row.unit,
            capture_key,
            _timestamp_text(row.captured_at) or "",
        )
        grouped[key].append(row)

    cohorts = []
    for key, items in grouped.items():
        provider, metric_code, platform, market, unit, _capture_key, captured_at = key
        comparator = _comparator(metric_code, unit)
        if comparator == "lower_is_stronger":
            items.sort(key=lambda item: (float(item.value), item.entity_label or "", item.provider_entity_id))
        elif comparator == "higher_is_stronger":
            items.sort(key=lambda item: (-float(item.value), item.entity_label or "", item.provider_entity_id))
        else:
            items.sort(key=lambda item: (item.entity_label or "", item.provider_entity_id))

        ordered_signals = []
        for index, item in enumerate(items, start=1):
            signal_key = (
                item.provider_name,
                item.entity_type,
                item.provider_entity_id,
                item.metric_code,
                item.platform,
                item.market_code,
            )
            previous = previous_by_key.get(signal_key)
            movement_places = None
            movement_state = "NOT_A_DAILY_RANK"
            movement_period_hours = None
            previous_position = None
            if item.provider_name == "Soundcharts" and item.metric_code.startswith("chart_position_global_"):
                movement_state = "HISTORY_PENDING"
                if previous is not None and previous.rights_basis == "licensed":
                    previous_position = float(previous.value)
                    current_observed = item.observed_at.replace(tzinfo=timezone.utc) if item.observed_at.tzinfo is None else item.observed_at.astimezone(timezone.utc)
                    previous_observed = previous.observed_at.replace(tzinfo=timezone.utc) if previous.observed_at.tzinfo is None else previous.observed_at.astimezone(timezone.utc)
                    movement_period_hours = round((current_observed - previous_observed).total_seconds() / 3600, 1)
                    if movement_period_hours >= 20:
                        movement_places = round(float(previous.value) - float(item.value), 2)
                        movement_state = "IMPROVED" if movement_places > 0 else "DECLINED" if movement_places < 0 else "UNCHANGED"
                    else:
                        movement_state = "SAME_SOURCE_DATE"
            ordered_signals.append({
                "position_in_cohort": index,
                "entity_type": item.entity_type,
                "entity_id": item.provider_entity_id,
                "title": item.entity_label,
                "artist": item.artist_label,
                "value": item.value,
                "unit": item.unit,
                "observed_at": _timestamp_text(item.observed_at),
                "captured_at": _timestamp_text(item.captured_at),
                "confidence": item.confidence,
                "coverage": item.coverage,
                "rights_basis": item.rights_basis,
                "canonical_entity_id": item.canonical_entity_id,
                "previous_position": previous_position,
                "movement_places": movement_places,
                "movement_state": movement_state,
                "movement_period_hours": movement_period_hours,
                "playlist_ready": False,
                "review_state": "REVIEW_ONLY",
            })

        cohorts.append({
            "provider": provider,
            "metric_code": metric_code,
            "platform": platform,
            "market_code": market,
            "unit": unit,
            "comparator": comparator,
            "captured_at": captured_at or None,
            "signal_count": len(items),
            "signals": ordered_signals,
        })

    cohorts.sort(key=lambda cohort: (cohort["captured_at"] or "", cohort["provider"], cohort["metric_code"]), reverse=True)
    # Give the review queue a cross-source and cross-market view. Filling one
    # cohort before moving to the next can hide every other region when the UI
    # requests its small default limit. Keep each cohort's native ordering and
    # take the same rank depth from each before extending the first cohort.
    selected_cohorts = [
        {**cohort, "signals": [], "signal_count_returned": 0}
        for cohort in cohorts
    ]
    remaining = bounded_limit
    while remaining:
        progressed = False
        for selected, cohort in zip(selected_cohorts, cohorts):
            index = selected["signal_count_returned"]
            if index >= len(cohort["signals"]):
                continue
            selected["signals"].append(cohort["signals"][index])
            selected["signal_count_returned"] += 1
            remaining -= 1
            progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break
    selected_cohorts = [cohort for cohort in selected_cohorts if cohort["signals"]]

    usable_provider_count = sum(1 for status in provider_statuses if _allowed_status(status))
    if counts["eligible_for_review"]:
        publication_state = "REVIEW_ONLY"
    elif counts["latest_signals_found"]:
        publication_state = "BLOCKED"
    else:
        publication_state = "WAITING_FOR_AUTHORIZED_DATA"

    return {
        "ordering_version": ORDERING_VERSION,
        "publication_state": publication_state,
        "ready_for_spotify": 0,
        "spotify_writes_enabled": False,
        "scan_limit": MAX_SCAN_ROWS,
        "scan_truncated": truncated,
        "summary": counts,
        "stages": [
            {
                "number": 1,
                "name": "Fuente y derechos",
                "state": "PASS" if usable_provider_count else "BLOCKED",
                "detail": "Solo proveedores conectados/verificados y rights_basis permitido por proveedor.",
            },
            {
                "number": 2,
                "name": "Calidad y antigüedad",
                "state": "PASS" if eligible else "WAITING",
                "detail": "Se conservan fechas observadas; no se inventa un umbral de frescura. Confianza y cobertura quedan desconocidas si el proveedor no las informa.",
            },
            {
                "number": 3,
                "name": "Orden comparable",
                "state": "READY" if eligible else "WAITING",
                "detail": "Orden nativo solo dentro de misma fuente, métrica, plataforma, mercado, unidad y captura; sin score combinado.",
            },
            {
                "number": 4,
                "name": "Encaje y publicación",
                "state": "WAITING",
                "detail": "Requiere resolver identidad de pista en catálogo Spotify, encaje con la playlist y revisión humana. No hay escritura automática.",
            },
        ],
        "blocked_reasons": blocked[:100],
        "cohorts": selected_cohorts,
        "method": {
            "rank_direction": "El puesto más bajo se ordena primero.",
            "growth_direction": "El crecimiento positivo más alto se ordena primero.",
            "unknown_metric": "Sin dirección semántica configurada, se conserva el orden alfabético estable dentro del lote.",
            "cross_source": "Soundcharts, Chartmetric y Last.fm no se mezclan ni corroboran sin identidad canónica y equivalencia de métrica.",
            "cohort_sampling": "La vista limitada recorre por turno fuentes, métricas y mercados para conservar amplitud regional antes de ampliar la profundidad de un solo grupo.",
            "movement": "El cambio de puesto se muestra solo para rankings Soundcharts global-N con dos fechas observadas separadas por al menos 20 horas; positivo significa que subió.",
            "freshness": "Sin umbral inventado: timestamps visibles y capturas más recientes primero entre cohortes.",
        },
    }
