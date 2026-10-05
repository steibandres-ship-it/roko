from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from dotenv import dotenv_values

from ..config import PROJECT_ROOT


SOUNDCHARTS_FREE_MODE = "free_1000_request_trial"
SOUNDCHARTS_FREE_REQUEST_CAP = 1_000
CHARTMETRIC_FREE_MODE = "free_7_day_trial"
CHARTMETRIC_FREE_TRIAL_LENGTH = timedelta(days=7)


def settings() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(PROJECT_ROOT / ".env").items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


def parse_utc(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def chartmetric_trial_window(values: dict[str, str] | None = None) -> tuple[datetime, datetime] | None:
    configured = values if values is not None else settings()
    if configured.get("CHARTMETRIC_ACCESS_MODE", "").strip() != CHARTMETRIC_FREE_MODE:
        return None
    started = parse_utc(configured.get("CHARTMETRIC_FREE_TRIAL_STARTED_AT", ""))
    ends = parse_utc(configured.get("CHARTMETRIC_FREE_TRIAL_ENDS_AT", ""))
    if started is None or ends is None or ends <= started:
        return None
    if ends - started > CHARTMETRIC_FREE_TRIAL_LENGTH:
        return None
    return started, ends


def chartmetric_free_trial_active(*, now: datetime | None = None, values: dict[str, str] | None = None) -> bool:
    return chartmetric_free_trial_state(now=now, values=values) == "FREE_TRIAL_ACTIVE"


def chartmetric_free_trial_state(*, now: datetime | None = None, values: dict[str, str] | None = None) -> str:
    window = chartmetric_trial_window(values)
    if window is None:
        return "FREE_TRIAL_WINDOW_REQUIRED"
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    started, ends = window
    if current < started:
        return "FREE_TRIAL_NOT_STARTED"
    if current >= ends:
        return "FREE_TRIAL_EXPIRED"
    return "FREE_TRIAL_ACTIVE"


def soundcharts_free_trial_enabled(values: dict[str, str] | None = None) -> bool:
    configured = values if values is not None else settings()
    return configured.get("SOUNDCHARTS_ACCESS_MODE", "").strip() == SOUNDCHARTS_FREE_MODE
