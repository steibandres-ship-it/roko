from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

from .config import DATA_DIR, Settings
from .logger import get_logger
from .storage import JsonStore


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
BASE_SCOPES = ["playlist-modify-public", "playlist-modify-private", "playlist-read-private", "user-read-private"]


class TokenStore:
    """OS keyring storage with a private local JSON fallback."""

    def __init__(self, client_id: str, fallback_path: Path | None = None):
        self.service = "spotify-playlist-network"
        self.account = client_id
        self.fallback = JsonStore(fallback_path or DATA_DIR / ".oauth_tokens.json")

    def load(self) -> dict[str, Any] | None:
        try:
            value = keyring.get_password(self.service, self.account)
            if value:
                return json.loads(value)
        except Exception:
            pass
        return self.fallback.read(None)

    def save(self, tokens: dict[str, Any]) -> str:
        serialized = json.dumps(tokens, separators=(",", ":"))
        try:
            keyring.set_password(self.service, self.account, serialized)
            try:
                if self.fallback.path.exists():
                    self.fallback.path.unlink()
            except OSError:
                pass
            return "OS keyring"
        except Exception:
            self.fallback.write(tokens, private=True)
            self._secure_windows_acl()
            return str(self.fallback.path)

    def delete(self) -> None:
        try:
            keyring.delete_password(self.service, self.account)
        except Exception:
            pass
        try:
            if self.fallback.path.exists():
                self.fallback.path.unlink()
        except OSError:
            pass

    def _secure_windows_acl(self) -> None:
        if os.name != "nt" or not self.fallback.path.exists():
            return
        account = f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\")
        try:
            subprocess.run(
                ["icacls", str(self.fallback.path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            get_logger().warning("Could not tighten Windows token file ACL (%s)", type(exc).__name__)


class OAuthError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = __import__("hashlib").sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class OAuthManager:
    def __init__(self, settings: Settings, token_store: TokenStore | None = None, *, timeout: float = 20.0):
        self.settings = settings
        self.token_store = token_store or TokenStore(settings.client_id)
        self.timeout = timeout
        self._refresh_lock = threading.Lock()

    def authorize(self, *, with_covers: bool = False, wait_seconds: int = 300) -> dict[str, Any]:
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        scopes = list(BASE_SCOPES)
        if with_covers:
            scopes.append("ugc-image-upload")
        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib interface
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                result.update({key: values[0] for key, values in query.items() if values})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Spotify authorization received</h1><p>You can close this tab and return to the terminal.</p></body></html>")

            def log_message(self, format: str, *args: Any) -> None:
                return

        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.settings.callback_port), CallbackHandler)
        except OSError as exc:
            raise OAuthError(f"Could not start local OAuth callback at {self.settings.redirect_uri}: {exc}") from exc
        server.timeout = 0.5
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            }
        )
        auth_url = f"{AUTH_URL}?{query}"
        print("Opening Spotify authorization in your browser. Sign in only on Spotify's website.")
        opened = webbrowser.open(auth_url)
        if not opened:
            print(f"If the browser does not open, copy this authorization URL into your browser: {auth_url}")
        deadline = time.monotonic() + wait_seconds
        try:
            while time.monotonic() < deadline and not result:
                server.handle_request()
        finally:
            server.server_close()
        if not result:
            raise OAuthError("Timed out waiting for Spotify's local callback. Run auth again.")
        if result.get("state") != state:
            raise OAuthError("OAuth state did not match. Authorization was rejected for safety; run auth again.")
        if result.get("error"):
            raise OAuthError(f"Spotify authorization was not granted: {result['error']}")
        code = result.get("code")
        if not code:
            raise OAuthError("Spotify callback did not include an authorization code.")
        try:
            response = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.redirect_uri,
                    "client_id": self.settings.client_id,
                    "code_verifier": verifier,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"Could not exchange the authorization code ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            raise OAuthError(f"Spotify token exchange failed (HTTP {response.status_code}). Check the Client ID and exact redirect URI.")
        tokens = response.json()
        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
        location = self.token_store.save(tokens)
        print(f"Session saved in {location}.")
        return tokens

    def access_token(self) -> str:
        with self._refresh_lock:
            tokens = self.token_store.load()
            if not tokens:
                raise OAuthError("No saved Spotify session. Run python -m app auth.")
            if int(tokens.get("expires_at", 0)) <= int(time.time()) + 60:
                refresh_token = tokens.get("refresh_token")
                if not refresh_token:
                    self.token_store.delete()
                    raise OAuthError("Spotify session expired. Run python -m app auth.")
                try:
                    response = httpx.post(
                        TOKEN_URL,
                        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self.settings.client_id},
                        timeout=self.timeout,
                    )
                except httpx.HTTPError as exc:
                    raise OAuthError(f"Could not refresh the Spotify session ({type(exc).__name__}). Run auth again if this persists.") from exc
                if response.status_code != 200:
                    self.token_store.delete()
                    raise OAuthError("Spotify authorization expired or is invalid. Run python -m app auth.")
                refreshed = response.json()
                tokens.update(refreshed)
                tokens["refresh_token"] = refreshed.get("refresh_token") or refresh_token
                tokens["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
                self.token_store.save(tokens)
            access_token = tokens.get("access_token")
            if not access_token:
                raise OAuthError("Saved Spotify session is incomplete. Run python -m app auth.")
            return str(access_token)
