from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import unicodedata
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MetricSnapshot
from .provider_ingest import provider_free_access_enabled, provider_rights_enabled
from .providers.base import ProviderStatus


PROVIDERS = ("Soundcharts", "Chartmetric")
ALLOWED_RIGHTS_BASIS = {"Soundcharts": "licensed", "Chartmetric": "licensed"}
USABLE_PROVIDER_STATES = {"CONNECTED", "DEGRADED"}
MAX_SCAN_ROWS = 20_000
MAX_GROUPS = 100


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value else None


def _identity_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _group_identity(row: MetricSnapshot) -> tuple[str, str] | None:
    if row.canonical_entity_id:
        return "canonical", row.canonical_entity_id
    title = _identity_text(row.entity_label)
    artist = _identity_text(row.artist_label)
    if not title or not artist:
        return None
    return "text", f"{title}\0{artist}"


def _metric_label(provider: str, metric_code: str) -> str:
    if provider == "Chartmetric":
        labels = {
            "chart_position": "Posición de chart",
            "weekly_growth_percent_spotify_plays": "Cambio semanal · reproducciones Spotify",
            "weekly_growth_percent_tiktok_posts": "Cambio semanal · publicaciones TikTok",
            "weekly_growth_percent_youtube_views": "Cambio semanal · visualizaciones YouTube",
            "weekly_growth_percent_shazam_count": "Cambio semanal · Shazam",
        }
        return labels.get(metric_code, metric_code)
    if metric_code.startswith("chart_position_"):
        return f"Puesto · chart {metric_code.removeprefix('chart_position_')}"
    if metric_code.startswith("chart_metric_"):
        return f"Métrica de chart · {metric_code.removeprefix('chart_metric_')}"
    return metric_code


def _provider_access(status: ProviderStatus | None, provider: str) -> dict[str, Any]:
    state = status.state if status else "PROVIDER_NOT_CONNECTED"
    rights_ok = provider_rights_enabled(provider)
    free_access_ok = provider_free_access_enabled(provider)
    connected = state in USABLE_PROVIDER_STATES
    usable = connected and rights_ok and free_access_ok
    blockers: list[str] = []
    if not connected:
        blockers.append(state)
    if not rights_ok:
        blockers.append("RIGHTS_SCOPE_REQUIRED")
    if not free_access_ok:
        blockers.append("FREE_ACCESS_MODE_INACTIVE")
    return {
        "state": state,
        "connected": connected,
        "rights_configured": rights_ok,
        "free_access_active": free_access_ok,
        "usable_for_comparison": usable,
        "blockers": list(dict.fromkeys(blockers)),
        "setup_action": status.setup_action if status else None,
        "last_refresh": _timestamp(status.last_refresh) if status else None,
        "markets_reported": list(status.country_coverage) if status else [],
        "metrics_reported": list(status.metrics_available) if status else [],
        "freshness_note": status.freshness_note if status else None,
        "rate_limit_status": status.rate_limit_status if status else "NOT_CHECKED",
    }


def build_provider_comparison(
    session: Session,
    provider_statuses: list[ProviderStatus],
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Show authorized Soundcharts and Chartmetric observations side by side.

    Text-based matches are review candidates only. Provider identifiers and metric
    definitions remain separate; no cross-provider score, delta, or consensus is made.
    """
    bounded_limit = min(max(limit, 1), MAX_GROUPS)
    statuses = {status.provider_name: status for status in provider_statuses}
    access = {name: _provider_access(statuses.get(name), name) for name in PROVIDERS}
    rows = session.scalars(
        select(MetricSnapshot)
        .where(MetricSnapshot.provider_name.in_(PROVIDERS))
        .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.observed_at.desc())
        .limit(MAX_SCAN_ROWS + 1)
    ).all()
    truncated = len(rows) > MAX_SCAN_ROWS
    rows = rows[:MAX_SCAN_ROWS]

    latest: dict[tuple[str, str, str, str, str, str], MetricSnapshot] = {}
    for row in rows:
        key = (
            row.provider_name,
            row.entity_type,
            row.provider_entity_id,
            row.metric_code,
            row.platform,
            row.market_code,
        )
        previous = latest.get(key)
        if previous is None or (_utc(row.captured_at), _utc(row.observed_at)) > (_utc(previous.captured_at), _utc(previous.observed_at)):
            latest[key] = row

    usable: list[MetricSnapshot] = []
    available_counts = {name: 0 for name in PROVIDERS}
    blocked_counts = {name: 0 for name in PROVIDERS}
    for row in latest.values():
        row_ok = (
            row.provider_name in PROVIDERS
            and access[row.provider_name]["usable_for_comparison"]
            and row.rights_basis == ALLOWED_RIGHTS_BASIS[row.provider_name]
            and row.entity_type == "track"
            and bool(row.metric_code and row.unit)
            and math.isfinite(float(row.value))
        )
        if row_ok:
            usable.append(row)
            available_counts[row.provider_name] += 1
        else:
            blocked_counts[row.provider_name] += 1

    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    unmatchable_counts = {name: 0 for name in PROVIDERS}
    now = datetime.now(timezone.utc)
    for row in usable:
        identity = _group_identity(row)
        if identity is None:
            unmatchable_counts[row.provider_name] += 1
            continue
        key = (identity[0], identity[1], row.platform.casefold(), row.market_code.upper())
        group = groups.setdefault(key, {
            "identity_type": "CANONICAL_ID" if identity[0] == "canonical" else "EXACT_TEXT_NEEDS_REVIEW",
            "canonical_entity_id": identity[1] if identity[0] == "canonical" else None,
            "title": row.entity_label,
            "artist": row.artist_label,
            "platform": row.platform,
            "market_code": row.market_code,
            "providers": {name: [] for name in PROVIDERS},
            "latest_capture": None,
        })
        if not group["title"] and row.entity_label:
            group["title"] = row.entity_label
        if not group["artist"] and row.artist_label:
            group["artist"] = row.artist_label
        captured_at = _utc(row.captured_at)
        if group["latest_capture"] is None or captured_at > group["latest_capture"]:
            group["latest_capture"] = captured_at
        age_hours = max(0.0, (now - captured_at).total_seconds() / 3600)
        if captured_at > now:
            freshness = "FUTURE_TIMESTAMP_REVIEW"
        elif age_hours > 72:
            freshness = "CAPTURE_OLDER_THAN_72H"
        else:
            freshness = "CAPTURE_WITHIN_72H"
        group["providers"][row.provider_name].append({
            "metric_code": row.metric_code,
            "metric_label": _metric_label(row.provider_name, row.metric_code),
            "value": float(row.value),
            "unit": row.unit,
            "observed_at": _timestamp(row.observed_at),
            "captured_at": _timestamp(row.captured_at),
            "capture_age_hours": round(age_hours, 1),
            "freshness_state": freshness,
            "confidence": row.confidence,
            "coverage": row.coverage,
            "rights_basis": row.rights_basis,
            "source_url": row.source_url,
        })

    output_groups: list[dict[str, Any]] = []
    paired_count = 0
    text_match_count = 0
    canonical_match_count = 0
    fresh_paired_count = 0
    unmatched_by_provider = {name: 0 for name in PROVIDERS}
    for group in groups.values():
        by_provider = group["providers"]
        has_soundcharts = bool(by_provider["Soundcharts"])
        has_chartmetric = bool(by_provider["Chartmetric"])
        if has_soundcharts and has_chartmetric:
            paired_count += 1
            if group["identity_type"] == "CANONICAL_ID":
                canonical_match_count += 1
            else:
                text_match_count += 1
            if any(item["freshness_state"] == "CAPTURE_WITHIN_72H" for item in by_provider["Soundcharts"]) and any(
                item["freshness_state"] == "CAPTURE_WITHIN_72H" for item in by_provider["Chartmetric"]
            ):
                fresh_paired_count += 1
            group["review_state"] = "PARALLEL_REVIEW"
        else:
            group["review_state"] = "ONLY_SOUNDCHARTS" if has_soundcharts else "ONLY_CHARTMETRIC"
            unmatched_by_provider["Soundcharts" if has_soundcharts else "Chartmetric"] += 1
        group["captured_at"] = _timestamp(group.pop("latest_capture"))
        for provider in PROVIDERS:
            by_provider[provider].sort(key=lambda item: (item["captured_at"] or "", item["metric_code"]), reverse=True)
        group["providers"] = by_provider
        group["numeric_delta"] = None
        group["metric_equivalence"] = "NOT_ASSERTED"
        output_groups.append(group)

    output_groups.sort(key=lambda group: (
        0 if group["review_state"] == "PARALLEL_REVIEW" else 1,
        0 if group["identity_type"] == "CANONICAL_ID" else 1,
        -(datetime.fromisoformat(group["captured_at"]).timestamp()) if group["captured_at"] else 0,
        (group["title"] or "").casefold(),
    ))
    output_groups = output_groups[:bounded_limit]
    both_usable = all(access[name]["usable_for_comparison"] for name in PROVIDERS)
    return {
        "state": "REVIEW_AVAILABLE" if both_usable else "BLOCKED_BY_PROVIDER_ACCESS",
        "providers": access,
        "summary": {
            "latest_signals_scanned": len(latest),
            "authorized_signals_available": len(usable),
            "available_signals_by_provider": available_counts,
            "paired_track_market_groups": paired_count,
            "fresh_paired_groups_within_72h": fresh_paired_count,
            "canonical_id_matches": canonical_match_count,
            "exact_text_matches_requiring_review": text_match_count,
            "unmatched_groups_by_provider": unmatched_by_provider,
            "blocked_signals_by_provider": blocked_counts,
            "missing_track_identity_by_provider": unmatchable_counts,
        },
        "groups": output_groups,
        "scan_limit": MAX_SCAN_ROWS,
        "scan_truncated": truncated,
        "method": {
            "identity": "Common canonical ID when available; otherwise exact normalized title+artist is a human-review candidate, never an automatic merge.",
            "dimensions": "Groups are kept separate by platform and market. Provider IDs and metric definitions stay source-specific.",
            "comparison": "Values are displayed side by side only. No delta, blended score, or consensus is calculated unless measurement equivalence is separately established.",
            "freshness": "Shows local capture age and source observation timestamp. Captures older than 72 hours or future-dated are visibly flagged; the provider's own coverage/confidence remain as reported (or unknown).",
            "playlist_gate": "Review is upstream of playlists. It does not authorize catalog identity, candidate selection, or Spotify writes.",
        },
    }
