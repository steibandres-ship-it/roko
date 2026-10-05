from __future__ import annotations

import logging
import re
import time
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)(\b(?:access_token|refresh_token|client_secret|code_verifier|authorization_code)\b\s*[:=]\s*)[^\s,;&]+"),
]


def sanitize_log_text(value: str) -> str:
    cleaned = value
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(r"\1[REDACTED]", cleaned)
    return cleaned


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: ("[REDACTED]" if key.lower() == "authorization" else value) for key, value in headers.items()}


def get_logger() -> logging.Logger:
    logger = logging.getLogger("spotify_playlist_network")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOGS_DIR / "spotify-playlist-network.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def log_api_call(operation: str, endpoint: str, status_code: int, track_count: int | None, result: str) -> None:
    count_text = "-" if track_count is None else str(track_count)
    match = re.search(r"/playlists/([^/?]+)", endpoint)
    playlist = match.group(1) if match else "-"
    get_logger().info(
        sanitize_log_text(
            "operation=%s playlist=%s endpoint=%s status=%s tracks=%s result=%s"
            % (operation, playlist, endpoint, status_code, count_text, result)
        )
    )
