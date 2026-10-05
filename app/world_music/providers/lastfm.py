from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import re
import time
from typing import Any

import httpx
from dotenv import dotenv_values

from ...config import PROJECT_ROOT
from .base import MusicDataProvider, ProviderStatus


API_URL = "https://ws.audioscrobbler.com/2.0/"
PROVIDER_NAME = "Last.fm"
COUNTRY_NAMES = {
    "AR": "Argentina",
    "BR": "Brazil",
    "CL": "Chile",
    "CO": "Colombia",
    "ES": "Spain",
    "MX": "Mexico",
    "US": "United States",
}


class LastFmNotConfigured(RuntimeError):
    pass


class LastFmScopeNotConfirmed(RuntimeError):
    pass


class LastFmAPIError(RuntimeError):
    def __init__(self, code: int | str, message: str = "UPSTREAM_ERROR"):
        self.code = str(code)
        super().__init__(f"Last.fm API request failed ({self.code}, {message})")


@dataclass(frozen=True, slots=True)
class LastFmResponse:
    payload: dict[str, Any]
    request_count: int
    error_code: str | None = None
    error_message: str | None = None
    cache_max_age_seconds: int = 0


def _settings() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(PROJECT_ROOT / ".env").items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


class LastFmProvider(MusicDataProvider):
    """Free, non-commercial Last.fm charts API adapter.

    The adapter only reads documented chart methods. It stores no raw response,
    user profile, or individual listen history. Snapshots are source-attributed
    and limited to a short local window by the ingestion job.
    """

    def __init__(
        self,
        api_key: str,
        *,
        noncommercial_scope_confirmed: bool = False,
        non_eea_permission_confirmed: bool = False,
        http_client: httpx.Client | None = None,
        api_url: str = API_URL,
    ):
        self._api_key = api_key.strip()
        self.noncommercial_scope_confirmed = noncommercial_scope_confirmed
        self.non_eea_permission_confirmed = non_eea_permission_confirmed
        self.api_url = api_url
        self._http = http_client
        self._owns_http = http_client is None
        self._request_budget: int | None = None
        self._requests_made = 0
        self._response_cache: dict[str, tuple[float, LastFmResponse]] = {}

    @classmethod
    def from_environment(cls) -> "LastFmProvider":
        values = _settings()
        api_key = values.get("LASTFM_API_KEY", "").strip()
        confirmed = values.get("LASTFM_NONCOMMERCIAL_USE", "false").strip().casefold() in {"1", "true", "yes"}
        non_eea_confirmed = values.get("LASTFM_NON_EEA_PERMISSION_CONFIRMED", "false").strip().casefold() in {"1", "true", "yes"}
        return cls(
            api_key,
            noncommercial_scope_confirmed=confirmed,
            non_eea_permission_confirmed=non_eea_confirmed,
        )

    @property
    def credentials_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def scope_confirmed(self) -> bool:
        return self.noncommercial_scope_confirmed and self.non_eea_permission_confirmed

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

    def _request(
        self,
        *,
        method: str,
        country: str | None,
        limit: int,
        extra_params: dict[str, str | int] | None = None,
    ) -> LastFmResponse:
        if not self.credentials_configured:
            raise LastFmNotConfigured("LASTFM_API_KEY is required")
        if not self.scope_confirmed:
            raise LastFmScopeNotConfirmed(
                "Confirm non-commercial use and the Last.fm API terms for use outside the EEA before fetching data."
            )
        if self._request_budget is not None and self._requests_made >= self._request_budget:
            raise LastFmAPIError("LOCAL_REQUEST_BUDGET_REACHED")
        params: dict[str, str | int] = {
            "method": method,
            "api_key": self._api_key,
            "format": "json",
            "limit": min(max(int(limit), 1), 100),
            "page": 1,
        }
        if country:
            params["country"] = country
        params.update(extra_params or {})
        cache_key = "|".join(f"{key}={value}" for key, value in sorted(params.items()) if key != "api_key")
        cached = self._response_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            value = cached[1]
            return LastFmResponse(
                payload=value.payload,
                request_count=0,
                cache_max_age_seconds=value.cache_max_age_seconds,
            )
        self._response_cache.pop(cache_key, None)
        self._requests_made += 1
        try:
            if self._http is None:
                self._http = httpx.Client(timeout=httpx.Timeout(25.0, connect=5.0))
            response = self._http.get(
                self.api_url,
                params=params,
                headers={"Accept": "application/json", "User-Agent": "WORLD-MUSIC-OS/0.2"},
            )
        except httpx.TimeoutException:
            raise LastFmAPIError("REQUEST_TIMEOUT") from None
        except httpx.RequestError:
            raise LastFmAPIError("UPSTREAM_CONNECTION_ERROR") from None
        if response.status_code >= 400:
            raise LastFmAPIError(response.status_code, "HTTP_REJECTED")
        try:
            payload = response.json()
        except ValueError:
            raise LastFmAPIError("INVALID_JSON") from None
        if not isinstance(payload, dict):
            raise LastFmAPIError("INVALID_RESPONSE")
        if payload.get("error") is not None:
            # Do not echo upstream request URLs or credentials in the error.
            raise LastFmAPIError(payload.get("error"), "API_REJECTED")
        cache_control = response.headers.get("Cache-Control", "").casefold()
        max_age_match = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", cache_control)
        cache_seconds = 0
        if "no-store" not in cache_control and "no-cache" not in cache_control:
            age_header = response.headers.get("Age", "0")
            try:
                response_age = max(0, int(age_header))
            except ValueError:
                response_age = 0
            if max_age_match:
                cache_seconds = max(0, int(max_age_match.group(1)) - response_age)
            else:
                expires_header = response.headers.get("Expires")
                if expires_header:
                    try:
                        expires_at = parsedate_to_datetime(expires_header)
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)
                        cache_seconds = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()) - response_age)
                    except (TypeError, ValueError, OverflowError):
                        cache_seconds = 0
        result = LastFmResponse(payload=payload, request_count=1, cache_max_age_seconds=cache_seconds)
        if cache_seconds > 0:
            self._response_cache[cache_key] = (time.monotonic() + cache_seconds, result)
        return result

    def fetch_top_tracks(self, market_code: str, *, limit: int = 100) -> LastFmResponse:
        market = market_code.strip().upper()
        if market == "GLOBAL":
            return self._request(method="chart.getTopTracks", country=None, limit=limit)
        country = COUNTRY_NAMES.get(market)
        if country is None:
            raise ValueError(f"unsupported Last.fm market: {market}")
        return self._request(method="geo.getTopTracks", country=country, limit=limit)

    def fetch_track_top_tags(self, artist: str, track: str, *, limit: int = 10) -> LastFmResponse:
        """Read documented Last.fm track tags for a one-off, attributed review."""
        if not artist.strip() or not track.strip():
            raise ValueError("artist and track are required")
        return self._request(
            method="track.getTopTags",
            country=None,
            limit=limit,
            extra_params={"artist": artist.strip()[:240], "track": track.strip()[:240], "autocorrect": 0},
        )

    def verify(self) -> ProviderStatus:
        response = self.fetch_top_tracks("GLOBAL", limit=1)
        if not _track_rows(response.payload):
            raise LastFmAPIError("EMPTY_CHART", "NO_TRACKS_RETURNED")
        return ProviderStatus(
            provider_name=PROVIDER_NAME,
            state="CONNECTED",
            country_coverage=["GLOBAL", *COUNTRY_NAMES],
            metrics_available=["Last.fm global chart rank", "Last.fm global chart playcount", "country chart rank", "country playcount for last week"],
            last_refresh=datetime.now(timezone.utc),
            data_confidence="INSUFFICIENT",
            rate_limit_status="NO_CURRENT_LIMIT_HEADER_REPORTED",
        )

    def status(self) -> ProviderStatus:
        coverage = ["GLOBAL", *COUNTRY_NAMES]
        available_metrics = [
            "Last.fm chart rank",
            "Last.fm chart playcount",
            "country chart playcount for last week",
        ]
        if not self.credentials_configured:
            setup_action = (
                "Create a free Last.fm API key after verifying the account, then set LASTFM_API_KEY; personal/non-commercial and outside-EEA consent are recorded."
                if self.scope_confirmed
                else "Create a free Last.fm API key, then confirm non-commercial scope and the outside-EEA permission condition before syncing."
            )
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="PROVIDER_NOT_CONNECTED",
                country_coverage=coverage,
                metrics_available=available_metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="API_KEY_REQUIRED",
                setup_action=setup_action,
                access_note="Default API use is non-commercial and requires Last.fm attribution/link plus caching. Outside the EEA, the terms require the relevant user opt-in; commercial or research use needs prior contact with Last.fm.",
                freshness_note="Country charts are weekly; the global chart's playcount window is not specified by Last.fm.",
            )
        if not self.scope_confirmed:
            return ProviderStatus(
                provider_name=PROVIDER_NAME,
                state="LICENSE_SCOPE_REQUIRED",
                country_coverage=coverage,
                metrics_available=available_metrics,
                data_confidence="INSUFFICIENT",
                rate_limit_status="NOT_CHECKED",
                setup_action="Confirm that the intended use fits Last.fm's non-commercial scope and the terms for use outside the EEA before syncing.",
                access_note="An API key alone does not grant commercial use or permission to publish Last.fm-powered data.",
                freshness_note="Country charts are weekly; the global chart's playcount window is not specified by Last.fm.",
            )
        return ProviderStatus(
            provider_name=PROVIDER_NAME,
            state="CONFIGURED_UNVERIFIED",
            country_coverage=coverage,
            metrics_available=available_metrics,
            data_confidence="INSUFFICIENT",
            rate_limit_status="NOT_REPORTED_BY_PROVIDER",
            setup_action="Run `python -m app world provider-check`, then `python -m app world sync`.",
            access_note="Use only within the documented non-commercial scope, with required attribution/link and caching; the out-of-EEA user opt-in condition still applies.",
            freshness_note="Country charts are weekly; the global chart's playcount window is not specified by Last.fm.",
        )


def _track_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = payload.get("tracks")
    rows = tracks.get("track") if isinstance(tracks, dict) else None
    if isinstance(rows, dict):
        return [rows]
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
