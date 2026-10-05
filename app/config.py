from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYLISTS_DIR = PROJECT_ROOT / "playlists"
DATA_DIR = PROJECT_ROOT / "data"
COVERS_DIR = PROJECT_ROOT / "covers"
LOGS_DIR = PROJECT_ROOT / "logs"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    request_timeout: float = Field(default=20.0, gt=0, le=120)
    max_retries: int = Field(default=4, ge=0, le=8)

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ValueError("redirect URI must use explicit loopback IPv4, e.g. http://127.0.0.1:8888/callback")
        if parsed.path != "/callback" or parsed.query or parsed.fragment:
            raise ValueError("redirect URI path must be /callback and must not include query or fragment")
        return value

    @property
    def callback_port(self) -> int:
        return urlparse(self.redirect_uri).port or 8888


def load_settings(*, require_client_id: bool = True, environ: dict[str, str] | None = None) -> Settings:
    file_values = dotenv_values(PROJECT_ROOT / ".env")
    source = dict(file_values)
    source.update(os.environ if environ is None else environ)
    client_id = str(source.get("SPOTIFY_CLIENT_ID") or "").strip()
    if not client_id and not require_client_id:
        client_id = "not-configured"
    redirect_uri = str(source.get("SPOTIFY_REDIRECT_URI") or "http://127.0.0.1:8888/callback").strip()
    try:
        return Settings(client_id=client_id, redirect_uri=redirect_uri)
    except ValidationError as exc:
        details = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
        if not client_id and require_client_id:
            raise ValueError("SPOTIFY_CLIENT_ID is missing. Copy .env.example to .env and add your Client ID.") from exc
        raise ValueError(f"Invalid Spotify settings: {details}") from exc


def ensure_project_dirs() -> None:
    for path in (DATA_DIR, COVERS_DIR, LOGS_DIR, DATA_DIR / "backups"):
        path.mkdir(parents=True, exist_ok=True)
