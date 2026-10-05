import httpx
import pytest

from app.config import Settings
from app.logger import sanitize_headers, sanitize_log_text
from app.spotify_client import SpotifyAPIError, SpotifyClient


def settings():
    return Settings(client_id="test-client")


def test_add_items_uses_current_items_route_and_batches_at_100():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.read(), request.headers.get("Authorization")))
        return httpx.Response(201, json={"snapshot_id": "snapshot"})

    items = [f"spotify:track:fake{i}" for i in range(205)]
    with SpotifyClient(settings(), transport=httpx.MockTransport(handler), token_provider=lambda: "unit-test-token") as spotify:
        spotify.add_items("playlist-id", items)
    assert [row[0] for row in seen] == ["/v1/playlists/playlist-id/items"] * 3
    assert [len(httpx.Response(200, content=row[1]).json()["uris"]) for row in seen] == [100, 100, 5]
    assert all(row[2] == "Bearer unit-test-token" for row in seen)


def test_429_retry_after_then_5xx_backoff_is_bounded_and_succeeds():
    statuses = [429, 503, 200]
    delays = []

    def handler(request):
        status = statuses.pop(0)
        if status == 429:
            return httpx.Response(status, headers={"Retry-After": "2"}, json={"error": "rate"})
        return httpx.Response(status, json={"ok": True})

    with SpotifyClient(
        settings(),
        transport=httpx.MockTransport(handler),
        token_provider=lambda: "test-token",
        sleep_fn=delays.append,
        random_fn=lambda low, high: 0.0,
        max_retries=3,
    ) as spotify:
        assert spotify.request("GET", "/me")["ok"] is True
    assert delays == [2.0, 1.0]
    assert statuses == []


def test_retry_count_is_finite():
    calls = []
    delays = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "temporary"})

    with SpotifyClient(
        settings(),
        transport=httpx.MockTransport(handler),
        token_provider=lambda: "test-token",
        sleep_fn=delays.append,
        random_fn=lambda low, high: 0.0,
        max_retries=2,
    ) as spotify:
        with pytest.raises(SpotifyAPIError, match="after retries"):
            spotify.request("GET", "/me")
    assert len(calls) == 3
    assert len(delays) == 2


def test_401_has_actionable_message_without_sensitive_details():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid token abc-secret"})

    with SpotifyClient(settings(), transport=httpx.MockTransport(handler), token_provider=lambda: "private-token") as spotify:
        with pytest.raises(SpotifyAPIError) as error:
            spotify.request("GET", "/me")
    assert "python -m app auth" in str(error.value)
    assert "private-token" not in str(error.value)
    assert "abc-secret" not in str(error.value)


def test_api_logs_sanitization_redacts_auth_and_token_values():
    text = "Authorization: Bearer abc.def; refresh_token=xyz; code_verifier=proof"
    cleaned = sanitize_log_text(text)
    assert "abc.def" not in cleaned
    assert "xyz" not in cleaned
    assert "proof" not in cleaned
    assert sanitize_headers({"Authorization": "Bearer full-token", "Accept": "application/json"}) == {
        "Authorization": "[REDACTED]",
        "Accept": "application/json",
    }


def test_remove_and_replace_use_playlist_items_endpoint_and_max_batches():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(200, json={"snapshot_id": "next"})

    with SpotifyClient(settings(), transport=httpx.MockTransport(handler), token_provider=lambda: "test-token") as spotify:
        spotify.remove_items("p", [f"spotify:track:id{i}" for i in range(101)], "before")
        spotify.replace_items("p", [f"spotify:track:new{i}" for i in range(100)])
    assert [(method, path) for method, path, _ in seen] == [
        ("DELETE", "/v1/playlists/p/items"),
        ("DELETE", "/v1/playlists/p/items"),
        ("PUT", "/v1/playlists/p/items"),
    ]
    first = httpx.Response(200, content=seen[0][2]).json()
    second = httpx.Response(200, content=seen[1][2]).json()
    replacement = httpx.Response(200, content=seen[2][2]).json()
    assert len(first["items"]) == 100
    assert first["snapshot_id"] == "before"
    assert second["snapshot_id"] == "next"
    assert len(replacement["uris"]) == 100
