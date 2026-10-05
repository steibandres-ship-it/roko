from __future__ import annotations

import base64
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from .auth import OAuthError, OAuthManager
from .config import Settings
from .logger import log_api_call


API_BASE = "https://api.spotify.com/v1"


class SpotifyAPIError(RuntimeError):
    def __init__(self, status_code: int | None, message: str):
        self.status_code = status_code
        super().__init__(message)


class SpotifyClient:
    def __init__(
        self,
        settings: Settings,
        auth: OAuthManager | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        token_provider: Callable[[], str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[float, float], float] = random.uniform,
        max_retries: int | None = None,
        max_retry_after: float = 300.0,
    ):
        self.settings = settings
        self.auth = auth or OAuthManager(settings, timeout=settings.request_timeout)
        self.token_provider = token_provider or self.auth.access_token
        self.sleep = sleep_fn
        self.random = random_fn
        self.max_retries = settings.max_retries if max_retries is None else max_retries
        self.max_retry_after = max_retry_after
        self.http = httpx.Client(base_url=API_BASE, timeout=settings.request_timeout, transport=transport)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "SpotifyClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        content: bytes | str | None = None,
        content_type: str = "application/json",
        operation: str | None = None,
        track_count: int | None = None,
    ) -> dict[str, Any]:
        route = endpoint.split("?", 1)[0]
        action = operation or method.lower()
        for attempt in range(self.max_retries + 1):
            try:
                token = self.token_provider()
                response = self.http.request(
                    method,
                    endpoint,
                    params=params,
                    json=json_body if content is None else None,
                    content=content,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
                )
            except OAuthError:
                raise
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    delay = min(30.0, 0.5 * (2**attempt)) + self.random(0.0, 0.25)
                    log_api_call(action, route, 0, track_count, f"network retry {attempt + 1}")
                    self.sleep(delay)
                    continue
                log_api_call(action, route, 0, track_count, "network failure")
                raise SpotifyAPIError(None, f"Spotify request failed ({type(exc).__name__}). Check your connection and try again.") from exc
            status = response.status_code
            if status == 429:
                retry_after = self._retry_after(response)
                log_api_call(action, route, status, track_count, f"rate limited; retry-after={retry_after:g}s")
                if attempt >= self.max_retries:
                    raise SpotifyAPIError(429, "Spotify rate limit exceeded after retries; try again later.")
                if retry_after > self.max_retry_after:
                    raise SpotifyAPIError(429, f"Spotify rate limit asks to wait {retry_after:g} seconds. Try again later.")
                print(f"Spotify rate limit reached. Retrying after {retry_after:g} seconds.")
                self.sleep(retry_after)
                continue
            if 500 <= status <= 599:
                log_api_call(action, route, status, track_count, "server error")
                if attempt < self.max_retries:
                    delay = min(30.0, 0.5 * (2**attempt)) + self.random(0.0, 0.25)
                    self.sleep(delay)
                    continue
                raise SpotifyAPIError(status, f"Spotify temporarily failed (HTTP {status}) after retries.")
            if status == 401:
                log_api_call(action, route, status, track_count, "authorization invalid")
                raise SpotifyAPIError(status, "Spotify authorization expired or is invalid. Run python -m app auth.")
            if status == 403:
                log_api_call(action, route, status, track_count, "forbidden")
                raise SpotifyAPIError(status, "Spotify denied this action. Check playlist ownership and OAuth scopes.")
            if status >= 400:
                log_api_call(action, route, status, track_count, "request rejected")
                if status == 404:
                    message = "Spotify resource was not found. Check its ID or playlist registration."
                else:
                    message = f"Spotify rejected the request (HTTP {status}). Check the configured data and API limits."
                raise SpotifyAPIError(status, message)
            if not response.content:
                log_api_call(action, route, status, track_count, "success")
                return {}
            try:
                value = response.json()
                data = value if isinstance(value, dict) else {"items": value}
            except ValueError:
                data = {}
            effective_count = track_count
            if effective_count is None and isinstance(data.get("items"), list):
                effective_count = len(data["items"])
            if effective_count is None and isinstance(data.get("tracks"), dict) and isinstance(data["tracks"].get("items"), list):
                effective_count = len(data["tracks"]["items"])
            log_api_call(action, route, status, effective_count, "success")
            return data
        raise AssertionError("bounded retry loop exited unexpectedly")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        value = response.headers.get("Retry-After", "1")
        try:
            return max(0.0, float(value))
        except (ValueError, TypeError):
            return 1.0

    def get_me(self) -> dict[str, Any]:
        return self.request("GET", "/me", operation="account")

    def get_track(self, track_id: str) -> dict[str, Any]:
        return self.request("GET", f"/tracks/{track_id}", operation="validate track")

    def search_tracks(self, query: str, *, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        data = self.request("GET", "/search", params={"q": query, "type": "track", "limit": min(max(limit, 1), 10), "offset": offset}, operation="search")
        return data.get("tracks", {}).get("items", [])

    def get_playlists(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self.request("GET", "/me/playlists", params={"limit": 50, "offset": offset}, operation="list playlists")
            page = data.get("items", [])
            items.extend(page)
            if not data.get("next") or not page:
                return items
            offset += len(page)

    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        return self.request("GET", f"/playlists/{playlist_id}", operation="read playlist")

    def get_playlist_items(self, playlist_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self.request(
                "GET", f"/playlists/{playlist_id}/items", params={"limit": 50, "offset": offset}, operation="read items"
            )
            page = data.get("items", [])
            items.extend(page)
            if not data.get("next") or not page:
                return items
            offset += len(page)

    def create_playlist(self, name: str, description: str, public: bool) -> dict[str, Any]:
        return self.request(
            "POST", "/me/playlists", json_body={"name": name, "description": description, "public": public, "collaborative": False}, operation="create playlist"
        )

    def update_playlist(self, playlist_id: str, *, name: str, description: str, public: bool) -> dict[str, Any]:
        return self.request(
            "PUT", f"/playlists/{playlist_id}", json_body={"name": name, "description": description, "public": public}, operation="update playlist"
        )

    def add_items(self, playlist_id: str, uris: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for start in range(0, len(uris), 100):
            batch = uris[start : start + 100]
            result = self.request(
                "POST", f"/playlists/{playlist_id}/items", json_body={"uris": batch}, operation="add items", track_count=len(batch)
            )
        return result

    def replace_items(self, playlist_id: str, uris: list[str]) -> dict[str, Any]:
        if len(uris) > 100:
            raise ValueError("Spotify accepts at most 100 URIs in one replace request")
        return self.request(
            "PUT", f"/playlists/{playlist_id}/items", json_body={"uris": uris}, operation="replace items", track_count=len(uris)
        )

    def remove_items(self, playlist_id: str, uris: list[str], snapshot_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for start in range(0, len(uris), 100):
            batch = uris[start : start + 100]
            body: dict[str, Any] = {"items": [{"uri": uri} for uri in batch]}
            if snapshot_id:
                body["snapshot_id"] = snapshot_id
            result = self.request(
                "DELETE", f"/playlists/{playlist_id}/items", json_body=body, operation="remove items", track_count=len(batch)
            )
            snapshot_id = result.get("snapshot_id", snapshot_id)
        return result

    def upload_cover(self, playlist_id: str, jpeg_bytes: bytes) -> dict[str, Any]:
        encoded = base64.b64encode(jpeg_bytes).decode("ascii")
        return self.request(
            "PUT", f"/playlists/{playlist_id}/images", content=encoded, content_type="image/jpeg", operation="upload cover"
        )
