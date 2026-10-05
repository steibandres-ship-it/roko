from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import time
from typing import Any, Mapping

import httpx
from dotenv import dotenv_values

from ...config import PROJECT_ROOT
from ..free_access import (
    CHARTMETRIC_FREE_MODE,
    chartmetric_free_trial_active,
    chartmetric_free_trial_state,
)
from .base import MusicDataProvider, ProviderStatus


API_BASE_URL = "https://api.chartmetric.com"
PROVIDER_NAME = "Chartmetric"
GROWTH_METRICS = {
    "spotify_plays": "spotify_plays",
    "tiktok_posts": "tiktok_posts",
    "youtube_views": "youtube_views",
    "shazam_count": "shazam_count",
}


class ChartmetricNotConfigured(RuntimeError):
    pass


class ChartmetricRightsNotConfirmed(RuntimeError):
    pass


class ChartmetricFreeTrialRequired(RuntimeError):
    pass


class ChartmetricAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(f"Chartmetric API request failed ({status_code}, {code})")


@dataclass(frozen=True, slots=True)
class ChartmetricResponse:
    payload: dict[str, Any]
    status_code: int
    request_count: int
    rate_limit: str | None


def _settings() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(PROJECT_ROOT / ".env").items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


class ChartmetricProvider(MusicDataProvider):
    """Server-side Chartmetric API adapter.

    Chartmetric's public terms restrict copying, persistent storage, and public
    redistribution. Ingestion therefore stays disabled until a separate written
    license explicitly permits snapshots, derived scores, and playlist curation.
    """

    def __init__(
        self,
        refresh_token: str,
        *,
        rights_confirmed: bool = False,
        base_url: str = API_BASE_URL,
        http_client: httpx.Client | None = None,
    ):
        self._refresh_token = refresh_token.strip()
        self.rights_confirmed = rights_confirmed
        self.base_url = base_url.rstrip("/")
        # Status checks do not need an HTTP client. Create it lazily so repeated
        # local dashboard polls do not allocate unused connection pools.
        self._http = http_client
        self._owns_http = http_client is None
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._request_budget: int | None = None
        self._requests_made = 0
        self._last_response: ChartmetricResponse | None = None

    @classmethod
    def from_environment(cls) -> "ChartmetricProvider | None":
        values = _settings()
        refresh_token = values.get("CHARTMETRIC_REFRESH_TOKEN", "").strip()
        if not refresh_token:
            return None
        confirmed = values.get("CHARTMETRIC_RIGHTS_CONFIRMED", "false").strip().casefold() in {"1", "true", "yes"}
        return cls(refresh_token, rights_confirmed=confirmed)

    @property
    def credentials_configured(self) -> bool:
        return bool(self._refresh_token)

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def set_request_budget(self, request_budget: int | None) -> None:
        self._request_budget = request_budget
        self._requests_made = 0

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            self._http.close()
            self._http = None

    def _budgeted_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._request_budget is not None and self._requests_made >= self._request_budget:
            raise ChartmetricAPIError(429, "LOCAL_REQUEST_BUDGET_REACHED")
        self._requests_made += 1
        try:
            if self._http is None:
                self._http = httpx.Client(timeout=httpx.Timeout(25.0, connect=5.0))
            return self._http.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise ChartmetricAPIError(504, "REQUEST_TIMEOUT") from None
        except httpx.RequestError:
            raise ChartmetricAPIError(502, "UPSTREAM_CONNECTION_ERROR") from None

    def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        if not self.credentials_configured:
            raise ChartmetricNotConfigured("CHARTMETRIC_REFRESH_TOKEN is required")
        response = self._budgeted_request(
            "POST",
            f"{self.base_url}/api/token",
            json={"refreshtoken": self._refresh_token},
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ChartmetricAPIError(response.status_code, "TOKEN_REQUEST_REJECTED")
        try:
            payload = response.json()
        except ValueError:
            raise ChartmetricAPIError(502, "TOKEN_RESPONSE_INVALID") from None
        if not isinstance(payload, dict):
            raise ChartmetricAPIError(502, "TOKEN_RESPONSE_INVALID")
        token = payload.get("token") or payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ChartmetricAPIError(502, "TOKEN_RESPONSE_INVALID")
        self._access_token = token
        try:
            lifetime = max(int(payload.get("expires_in", 3600)), 60)
        except (TypeError, ValueError):
            lifetime = 3600
        self._token_expires_at = now + lifetime
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        retries: int = 2,
    ) -> ChartmetricResponse:
        if not self.rights_confirmed:
            raise ChartmetricRightsNotConfirmed(
                "No Chartmetric data request made: written permission for stored snapshots, derived scores, and playlist curation is required."
            )
        if not chartmetric_free_trial_active():
            raise ChartmetricFreeTrialRequired(
                "Chartmetric is restricted to an explicitly configured, active 7-day free API trial; no request was made."
            )
        token = self._get_access_token()
        request_count = 0
        for attempt in range(retries + 1):
            request_count += 1
            response = self._budgeted_request(
                method,
                f"{self.base_url}/{path.lstrip('/')}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                token = self._get_access_token()
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= retries:
                    raise ChartmetricAPIError(response.status_code, "UPSTREAM_RETRY_LIMIT")
                retry_after = response.headers.get("Retry-After", "")
                delay = min(float(retry_after), 20.0) if retry_after.isdigit() else min(2**attempt, 8)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise ChartmetricAPIError(response.status_code, "UPSTREAM_REJECTED")
            try:
                payload = response.json()
            except ValueError:
                raise ChartmetricAPIError(502, "UPSTREAM_RESPONSE_INVALID") from None
            if not isinstance(payload, dict):
                raise ChartmetricAPIError(502, "UPSTREAM_RESPONSE_INVALID")
            rate_limit = response.headers.get("X-RateLimit-Remaining")
            if rate_limit is None:
                rate_limit = response.headers.get("X-RateLimit-Reset")
            result = ChartmetricResponse(payload, response.status_code, request_count, rate_limit)
            self._last_response = result
            return result
        raise ChartmetricAPIError(503, "UPSTREAM_RETRY_LIMIT")

    def verify(self) -> ProviderStatus:
        """Confirm API auth and plan access with an uncached metadata read; no rows are saved."""
        if not self.rights_confirmed:
            raise ChartmetricRightsNotConfirmed(
                "No Chartmetric data request made: written permission for local snapshots, derived scores, and playlist curation is required."
            )
        result = self._request("GET", "/api/artist/206557")
        return ProviderStatus(
            provider_name=PROVIDER_NAME,
            state="CONNECTED_UNVERIFIED_RIGHTS" if not self.rights_confirmed else "CONNECTED",
            country_coverage=[],
            metrics_available=["chartmetric_artist_metadata_test"],
            last_refresh=datetime.now(timezone.utc),
            data_confidence="INSUFFICIENT",
            rate_limit_status=f"RATE_LIMIT_REMAINING:{result.rate_limit}" if result.rate_limit else "RESPONSE_OK_LIMIT_UNKNOWN",
        )

    def fetch_growth_tracks(self, platform_metric: str, *, limit: int = 100) -> ChartmetricResponse:
        if not self.rights_confirmed:
            raise ChartmetricRightsNotConfirmed("Written permission for stored snapshots, derived scores, and playlist curation is required.")
        metric = GROWTH_METRICS.get(platform_metric)
        if metric is None:
            raise ValueError("unsupported growth metric")
        return self._request(
            "GET",
            "/api/track/list/filter",
            params={
                "range_period": "weekly_diff_percent",
                "sortColumn": f"weekly_diff_percent.{metric}",
                "sortOrderDesc": "true",
                "originalsOnly": "true",
                "limit": min(max(int(limit), 1), 100),
                "offset": 0,
            },
        )

    def fetch_chart(
        self,
        platform: str,
        country_code: str,
        *,
        limit: int = 100,
    ) -> ChartmetricResponse:
        if not self.rights_confirmed:
            raise ChartmetricRightsNotConfirmed("Written permission for stored snapshots, derived scores, and playlist curation is required.")
        country = country_code.strip().upper()
        if country != "GLOBAL" and (len(country) != 2 or not country.isalpha()):
            raise ValueError("country_code must be an ISO alpha-2 code or GLOBAL")
        count = min(max(int(limit), 1), 100)
        if platform == "spotify":
            return self._request(
                "GET",
                "/api/charts/spotify",
                params={
                    "country_code": country,
                    "interval": "daily",
                    "type": "regional",
                    "latest": "true",
                    "limit": count,
                    "offset": 0,
                },
            )
        if platform == "apple_music":
            return self._request(
                "GET",
                "/api/charts/applemusic/tracks",
                params={
                    "country_code": country,
                    "type": "daily",
                    "latest": "true",
                    "limit": count,
                    "offset": 0,
                },
            )
        raise ValueError("supported chart platforms are spotify and apple_music")

    def status(self) -> ProviderStatus:
        metrics = [
            "Spotify country/global daily charts",
            "Apple Music country/global daily charts",
            "weekly percent changes: Spotify, TikTok, YouTube, Shazam",
            "track release dates when supplied",
        ]
        if not self.credentials_configured:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="PROVIDER_NOT_CONNECTED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="API_CREDENTIALS_REQUIRED",
                setup_action="The free dashboard is not API access. Request an API refresh token from hi@chartmetric.com, then activate only the 7-day no-card API trial and configure its actual UTC window locally.",
                access_note=(
                    "Free-only mode allows the API & MCP 7-day trial advertised as no-card; paid dashboard plans and metered usage are disabled. The free dashboard cannot be scraped. "
                    "Written rights must separately allow local snapshots, derived scores, and public-playlist curation."
                ),
                freshness_note="No Chartmetric metrics have been fetched.",
            )
        if not self.rights_confirmed:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="LICENSE_SCOPE_REQUIRED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="NOT_CHECKED",
                setup_action="Obtain written permission for storing observations, computing derived scores, and using those scores for public Spotify playlist research.",
                access_note="No Chartmetric data request or local save is permitted until this scope is documented.",
                freshness_note="No Chartmetric metrics have been fetched.",
            )
        trial_state = chartmetric_free_trial_state()
        if trial_state != "FREE_TRIAL_ACTIVE":
            action = (
                f"After manually activating the free 7-day API trial, set CHARTMETRIC_ACCESS_MODE={CHARTMETRIC_FREE_MODE} and its actual UTC start/end timestamps in .env."
                if trial_state == "FREE_TRIAL_WINDOW_REQUIRED"
                else "Chartmetric free API trial has not started yet; no calls are allowed until its configured start time."
                if trial_state == "FREE_TRIAL_NOT_STARTED"
                else "Chartmetric's free API trial has expired; this free-only setup will not use a paid plan."
            )
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="CONFIGURED_UNVERIFIED",
                metrics_available=metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status=trial_state,
                setup_action=action,
                access_note=(
                    "Only the manually activated 7-day API trial is enabled. A free Chartmetric dashboard account does not provide API access. "
                    "Written rights for local snapshots, derived scores, and playlist curation remain a separate requirement."
                ),
                freshness_note="No Chartmetric metrics have been fetched outside an active free-trial window.",
            )
        return ProviderStatus(
            provider_name=PROVIDER_NAME,
            state="CONFIGURED_UNVERIFIED",
            metrics_available=metrics,
            data_confidence="INSUFFICIENT",
            rate_limit_status=f"RATE_LIMIT_REMAINING:{self._last_response.rate_limit}" if self._last_response and self._last_response.rate_limit else "NOT_CHECKED",
            setup_action="Run `python -m app world chartmetric-check` to verify API access before the bounded sync.",
            access_note="Free-only mode is limited to the configured 7-day API trial; no paid plan or post-trial requests are enabled.",
            freshness_note="Weekly growth deltas and latest dated charts are refreshed according to Chartmetric's source windows.",
        )
