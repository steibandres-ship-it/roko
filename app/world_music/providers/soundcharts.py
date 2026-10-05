from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time
from typing import Any, Mapping
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

from ...config import PROJECT_ROOT
from ..free_access import SOUNDCHARTS_FREE_MODE, SOUNDCHARTS_FREE_REQUEST_CAP, soundcharts_free_trial_enabled
from .base import MusicDataProvider, ProviderStatus


API_BASE_URL = "https://customer.api.soundcharts.com"
TOKEN_URL = "https://account.soundcharts.com/oauth/token"
PROVIDER_NAME = "Soundcharts"


class SoundchartsNotConfigured(RuntimeError):
    pass


class SoundchartsRightsNotConfirmed(RuntimeError):
    pass


class SoundchartsFreeAccessRequired(SoundchartsNotConfigured):
    pass


class SoundchartsAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(f"Soundcharts API request failed ({status_code}, {code})")


def _settings() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(PROJECT_ROOT / ".env").items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


def _flag(values: Mapping[str, str], name: str) -> bool:
    return values.get(name, "false").strip().casefold() in {"1", "true", "yes"}


def _parse_verified_at(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class SoundchartsProvider(MusicDataProvider):
    """Server-side adapter for the official Soundcharts API.

    The adapter supports the current OAuth client-credentials flow and legacy
    API headers. Music-data reads require an explicit local rights gate because
    the app stores snapshots and uses them for editorial playlist research.
    """

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        *,
        team_id: str = "",
        legacy_app_id: str = "",
        legacy_api_key: str = "",
        rights_confirmed: bool = False,
        base_url: str = API_BASE_URL,
        token_url: str = TOKEN_URL,
        http_client: httpx.Client | None = None,
        last_verified_at: datetime | None = None,
        quota_remaining: str | None = None,
    ):
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._team_id = team_id.strip()
        self._legacy_app_id = legacy_app_id.strip()
        self._legacy_api_key = legacy_api_key.strip()
        self.rights_confirmed = rights_confirmed
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self._http = http_client
        self._owns_http = http_client is None
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._last_verified_at = last_verified_at
        self._quota_remaining = quota_remaining
        self._requests_made = 0
        self._request_budget: int | None = None

    @classmethod
    def from_environment(cls) -> "SoundchartsProvider | None":
        values = _settings()
        if not any(values.get(name, "").strip() for name in (
            "SOUNDCHARTS_CLIENT_ID", "SOUNDCHARTS_CLIENT_SECRET",
            "SOUNDCHARTS_APP_ID", "SOUNDCHARTS_API_KEY",
        )):
            return None
        return cls(
            values.get("SOUNDCHARTS_CLIENT_ID", ""),
            values.get("SOUNDCHARTS_CLIENT_SECRET", ""),
            team_id=values.get("SOUNDCHARTS_TEAM_ID", ""),
            legacy_app_id=values.get("SOUNDCHARTS_APP_ID", ""),
            legacy_api_key=values.get("SOUNDCHARTS_API_KEY", ""),
            rights_confirmed=_flag(values, "SOUNDCHARTS_RIGHTS_CONFIRMED"),
            last_verified_at=_parse_verified_at(values.get("SOUNDCHARTS_API_VERIFIED_AT")),
            quota_remaining=values.get("SOUNDCHARTS_API_QUOTA_REMAINING", "").strip() or None,
        )

    @property
    def credentials_configured(self) -> bool:
        oauth_pair = bool(self._client_id and self._client_secret)
        legacy_pair = bool(self._legacy_app_id and self._legacy_api_key)
        return oauth_pair or legacy_pair

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def set_request_budget(self, request_budget: int | None) -> None:
        if request_budget is not None and request_budget < 0:
            raise ValueError("request_budget must not be negative")
        self._request_budget = min(request_budget, SOUNDCHARTS_FREE_REQUEST_CAP) if request_budget is not None else SOUNDCHARTS_FREE_REQUEST_CAP
        self._requests_made = 0

    @property
    def quota_remaining(self) -> str | None:
        return self._quota_remaining

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            self._http.close()
            self._http = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=httpx.Timeout(25.0, connect=5.0))
        return self._http

    def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        if not self._client_id or not self._client_secret:
            raise SoundchartsNotConfigured("OAuth client credentials are required")
        form: dict[str, str] = {"grant_type": "client_credentials"}
        if self._team_id:
            form["team_id"] = self._team_id
        try:
            response = self._client().post(
                self.token_url,
                data=form,
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise SoundchartsAPIError(504, "TOKEN_REQUEST_TIMEOUT") from None
        except httpx.RequestError:
            raise SoundchartsAPIError(502, "TOKEN_CONNECTION_ERROR") from None
        if response.status_code >= 400:
            raise SoundchartsAPIError(response.status_code, "TOKEN_REQUEST_REJECTED")
        try:
            payload = response.json()
        except ValueError:
            raise SoundchartsAPIError(502, "TOKEN_RESPONSE_INVALID") from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise SoundchartsAPIError(502, "TOKEN_RESPONSE_INVALID")
        try:
            lifetime = max(int(payload.get("expires_in", 3600)), 60)
        except (TypeError, ValueError):
            lifetime = 3600
        self._access_token = token
        self._token_expires_at = now + lifetime
        return token

    def _request(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        usage_probe: bool = False,
    ) -> dict[str, Any]:
        if not soundcharts_free_trial_enabled():
            raise SoundchartsFreeAccessRequired(
                f"Free-only mode is active. Set SOUNDCHARTS_ACCESS_MODE={SOUNDCHARTS_FREE_MODE} only for the official 1,000-call free trial."
            )
        if not self.credentials_configured:
            raise SoundchartsNotConfigured("Soundcharts API credentials are required")
        if not usage_probe and not self.rights_confirmed:
            raise SoundchartsRightsNotConfirmed(
                "Written permission for local metric snapshots and playlist curation is required."
            )
        effective_budget = min(
            SOUNDCHARTS_FREE_REQUEST_CAP,
            self._request_budget if self._request_budget is not None else SOUNDCHARTS_FREE_REQUEST_CAP,
        )
        if not usage_probe and self._requests_made >= effective_budget:
            raise SoundchartsAPIError(429, "LOCAL_FREE_TRIAL_REQUEST_CAP_REACHED")
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._client_id and self._client_secret:
            headers["Authorization"] = f"Bearer {self._get_access_token()}"
        elif self._legacy_app_id and self._legacy_api_key:
            headers["x-app-id"] = self._legacy_app_id
            headers["x-api-key"] = self._legacy_api_key
        else:
            raise SoundchartsNotConfigured("Set both OAuth client credentials or both legacy API headers")
        # Soundcharts documents the team usage probe as outside the music-data
        # quota. Count only billable/limited data endpoints toward the local cap.
        if not usage_probe:
            self._requests_made += 1
        try:
            response = self._client().get(
                f"{self.base_url}/{path.lstrip('/')}", params=params, headers=headers
            )
        except httpx.TimeoutException:
            raise SoundchartsAPIError(504, "REQUEST_TIMEOUT") from None
        except httpx.RequestError:
            raise SoundchartsAPIError(502, "UPSTREAM_CONNECTION_ERROR") from None
        self._quota_remaining = response.headers.get("x-quota-remaining") or self._quota_remaining
        if response.status_code >= 400:
            raise SoundchartsAPIError(response.status_code, "UPSTREAM_REJECTED")
        try:
            payload = response.json()
        except ValueError:
            raise SoundchartsAPIError(502, "UPSTREAM_RESPONSE_INVALID") from None
        if not isinstance(payload, dict):
            raise SoundchartsAPIError(502, "UPSTREAM_RESPONSE_INVALID")
        return payload

    def verify(self) -> ProviderStatus:
        """Check API authentication using Soundcharts' no-quota usage endpoint."""
        if not self.credentials_configured:
            raise SoundchartsNotConfigured("Soundcharts API credentials are required")
        payload = self._request("/api/v2/team/usage", usage_probe=True)
        usage = payload.get("object", payload.get("data", payload))
        quota = self._quota_remaining
        if quota is None and isinstance(usage, dict):
            quota = usage.get("quotaRemaining") or usage.get("quota_remaining")
        self._quota_remaining = str(quota) if quota is not None else None
        self._last_verified_at = datetime.now(timezone.utc)
        return self.status()

    def fetch_song_chart_catalog(
        self,
        platform: str,
        *,
        country_code: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        platform_code = platform.strip().casefold()
        if not platform_code or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in platform_code):
            raise ValueError("platform must be a Soundcharts platform code")
        if country_code and (len(country_code.strip()) != 2 or not country_code.strip().isalpha()):
            raise ValueError("country_code must be an ISO alpha-2 code")
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 100), "offset": 0}
        if country_code:
            params["countryCode"] = country_code.strip().upper()
        return self._request(f"/api/v2/chart/song/by-platform/{quote(platform_code, safe='-_')}", params=params)

    def fetch_latest_song_chart(self, chart_slug: str, *, limit: int = 100) -> dict[str, Any]:
        slug = chart_slug.strip()
        if not slug or "/" in slug or ".." in slug or "?" in slug or "#" in slug:
            raise ValueError("chart_slug is invalid")
        return self._request(
            f"/api/v2.14/chart/song/{quote(slug, safe='-_')}/ranking/latest",
            params={"limit": min(max(int(limit), 1), 100), "offset": 0},
        )

    def status(self) -> ProviderStatus:
        metrics = [
            "Chart positions; stream counts when present in Spotify charts",
            "Airplay spins, YouTube chart views, and TikTok chart video counts",
            "Spotify, YouTube, TikTok, and airplay chart metrics where the chart reports a value",
        ]
        access_note = (
            "Free-only mode permits the official 1,000-request free API trial. The open sandbox has limited test data "
            "and is not treated as current trends. No paid plan is enabled; stored snapshots and playlist research "
            "still require the applicable written rights."
        )
        if not self.credentials_configured:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="PROVIDER_NOT_CONNECTED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="API_CREDENTIALS_REQUIRED",
                setup_action="Create API credentials through Soundcharts' free 1,000-request trial, then store them locally; a dashboard login is not an API key.",
                access_note=access_note,
                freshness_note="The provider returns endpoint-specific ranking dates; no source data has been fetched.",
            )
        if not self.rights_confirmed:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="LICENSE_SCOPE_REQUIRED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="NOT_CHECKED",
                setup_action="Obtain written permission covering local history, derived scores, and public Spotify playlist research.",
                access_note=access_note,
                freshness_note="No Soundcharts music data is being read or stored.",
            )
        if not soundcharts_free_trial_enabled():
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="CONFIGURED_UNVERIFIED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="FREE_TRIAL_MODE_REQUIRED",
                setup_action=f"Set SOUNDCHARTS_ACCESS_MODE={SOUNDCHARTS_FREE_MODE} only after enabling the free 1,000-request API trial.",
                access_note=access_note,
                freshness_note="No Soundcharts music data is being read or stored until free-trial mode is configured.",
            )
        verification_age = (
            datetime.now(timezone.utc) - self._last_verified_at
            if self._last_verified_at is not None
            else None
        )
        verification_fresh = (
            verification_age is not None
            and timedelta(0) <= verification_age <= timedelta(hours=72)
        )
        if not verification_fresh:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="CONFIGURED_UNVERIFIED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="VERIFICATION_STALE" if self._last_verified_at else "NOT_CHECKED",
                setup_action="Run `python -m app world soundcharts-check` to verify API authentication and quota.",
                access_note=access_note,
                freshness_note=(
                    "The last API authentication check is older than 72 hours; reverify before reading music data."
                    if self._last_verified_at
                    else "Endpoint-specific ranking dates will be shown after a data connection is verified."
                ),
            )
        return ProviderStatus(
            provider_name=PROVIDER_NAME,
            state="CONNECTED",
            metrics_available=metrics,
            last_refresh=self._last_verified_at,
            data_confidence="INSUFFICIENT",
            rate_limit_status=f"QUOTA_REMAINING:{self._quota_remaining}" if self._quota_remaining else "RATE_LIMIT_NOT_REPORTED",
            access_note=access_note,
            freshness_note="API authentication verified; data freshness is determined per returned chart date.",
        )
