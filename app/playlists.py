from __future__ import annotations

import io
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from .config import DATA_DIR, PLAYLISTS_DIR
from .models import PlaylistConfig
from .spotify_client import SpotifyAPIError
from .storage import JsonStore, PlaylistRegistry, utc_now


class PlaylistConfigError(ValueError):
    pass


def load_playlist(path: Path) -> PlaylistConfig:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        playlist = PlaylistConfig.model_validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise PlaylistConfigError(f"Invalid playlist configuration {path.name}: {exc}") from exc
    if path.stem != playlist.slug:
        raise PlaylistConfigError(f"Playlist file {path.name} must match its slug {playlist.slug}.json")
    return playlist


def load_playlists(directory: Path | None = None) -> list[PlaylistConfig]:
    root = directory or PLAYLISTS_DIR
    configs = [load_playlist(path) for path in sorted(root.glob("*.json"))]
    slugs = [config.slug for config in configs]
    if len(slugs) != len(set(slugs)):
        raise PlaylistConfigError("Playlist slugs must be unique")
    return configs


def get_playlist_config(slug: str, directory: Path | None = None) -> PlaylistConfig:
    for playlist in load_playlists(directory):
        if playlist.slug == slug:
            return playlist
    raise PlaylistConfigError(f"Unknown playlist slug: {slug}")


def prepare_cover(path: Path) -> bytes:
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("cover must use a .jpg or .jpeg filename")
    try:
        with Image.open(path) as source:
            if source.format != "JPEG":
                raise ValueError("cover contents are not a JPEG image")
            width, height = source.size
            if max(width, height) > 10000 or width * height > 40_000_000:
                raise ValueError("cover exceeds the local image-dimension safety limit")
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width != height:
                raise ValueError("Spotify covers must be square; create a square JPG and try again")
            if min(width, height) < 300:
                raise ValueError("cover dimensions must be at least 300 × 300 pixels")
            if max(width, height) > 4096:
                image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            image = image.convert("RGB")
            for quality in (92, 86, 80, 74, 68, 62, 56, 50):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
                result = buffer.getvalue()
                if 4 * ((len(result) + 2) // 3) <= 256 * 1024:
                    return result
            raise ValueError("could not compress cover to Spotify's 256 KB encoded-image limit")
    except UnidentifiedImageError as exc:
        raise ValueError("cover is not a readable JPEG image") from exc


def resolve_cover_path(cover: str | None) -> Path | None:
    if not cover:
        return None
    candidate = Path(cover)
    return candidate if candidate.is_absolute() else (PLAYLISTS_DIR.parent / candidate)


def verify_cover_scope(token_data: dict[str, Any] | None) -> bool:
    return "ugc-image-upload" in set((token_data or {}).get("scope", "").split())


def playlist_url(data: dict[str, Any]) -> str:
    return str((data.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{data.get('id', '')}")


def create_one(
    spotify: Any,
    config: PlaylistConfig,
    registry: PlaylistRegistry,
    owner_id: str,
    *,
    token_data: dict[str, Any] | None = None,
    known_playlists: list[dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    existing = registry.get(config.slug)
    if existing:
        if existing.get("owner_id") and existing.get("owner_id") != owner_id:
            raise ValueError(f"Registry playlist {config.slug} belongs to a different Spotify account.")
        saved_url = existing.get("spotify_url") or f"https://open.spotify.com/playlist/{existing['spotify_playlist_id']}"
        return str(saved_url), False

    possible = [
        item for item in (known_playlists if known_playlists is not None else spotify.get_playlists())
        if str(item.get("name", "")).casefold() == config.name.casefold()
        and str((item.get("owner") or {}).get("id") or "") == owner_id
    ]
    if possible:
        urls = ", ".join(playlist_url(item) for item in possible)
        raise ValueError(f"An unregistered playlist with this name already exists: {urls}. Run playlists recover {config.slug} and confirm the match.")

    cover_path = resolve_cover_path(config.cover)
    cover_bytes: bytes | None = None
    if cover_path and cover_path.is_file():
        if "ugc-image-upload" not in set((token_data or {}).get("scope", "").split()):
            raise ValueError("This playlist has a cover, but the Spotify session lacks ugc-image-upload. Run python -m app auth --with-covers.")
        cover_bytes = prepare_cover(cover_path)

    created = spotify.create_playlist(config.name, config.description, config.public)
    playlist_id = str(created["id"])
    now = utc_now()
    registry.update(
        config.slug,
        {
            "spotify_playlist_id": playlist_id,
            "spotify_url": playlist_url(created),
            "created_at": now,
            "owner_id": owner_id,
            "last_synced": None,
            "locked_uris": [],
            **({"cover_sha256": hashlib.sha256(cover_bytes).hexdigest()} if cover_bytes is not None else {}),
        },
    )
    if cover_bytes is None:
        print(f"{config.slug}: Cover skipped: no custom image configured")
    else:
        spotify.upload_cover(playlist_id, cover_bytes)
        registry.update(config.slug, {"cover_sha256": hashlib.sha256(cover_bytes).hexdigest()})
    return playlist_url(created), True


def recover_matches(spotify: Any, configs: list[PlaylistConfig], *, owner_id: str) -> dict[str, list[dict[str, Any]]]:
    owned = [item for item in spotify.get_playlists() if str((item.get("owner") or {}).get("id") or "") == owner_id]
    matches: dict[str, list[dict[str, Any]]] = {}
    for config in configs:
        found = [item for item in owned if str(item.get("name", "")).casefold() == config.name.casefold()]
        if found:
            matches[config.slug] = found
    return matches


def store_recovered_playlist(config: PlaylistConfig, item: dict[str, Any], registry: PlaylistRegistry, owner_id: str) -> None:
    registry.update(
        config.slug,
        {
            "spotify_playlist_id": item["id"],
            "spotify_url": playlist_url(item),
            "created_at": None,
            "recovered_at": utc_now(),
            "owner_id": owner_id,
            "last_synced": None,
            "locked_uris": [],
        },
    )


def create_backup(
    slug: str,
    playlist_id: str,
    playlist: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    backup_root: Path | None = None,
) -> Path:
    root = backup_root or DATA_DIR / "backups"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    directory = root / today
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    path = directory / f"{slug}-{stamp}.json"
    tracks = []
    for entry in items:
        item = entry.get("item") or entry.get("track") or {}
        if not item or not item.get("uri"):
            continue
        tracks.append({"spotify_uri": item["uri"], "name": item.get("name"), "artists": [a.get("name") for a in item.get("artists", [])]})
    backup = {
        "slug": slug,
        "playlist_id": playlist_id,
        "snapshot_id": playlist.get("snapshot_id"),
        "created_at": utc_now(),
        "name": playlist.get("name"),
        "description": playlist.get("description"),
        "public": playlist.get("public"),
        "tracks": tracks,
    }
    JsonStore(path).write(backup)
    return path


def read_backup(path: Path, *, backup_root: Path | None = None) -> dict[str, Any]:
    root = (backup_root or DATA_DIR / "backups").resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(root):
        raise ValueError("restore only accepts backup files inside data/backups")
    try:
        backup = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read backup: {exc}") from exc
    if not isinstance(backup, dict) or not backup.get("slug") or not backup.get("playlist_id") or not isinstance(backup.get("tracks"), list):
        raise ValueError("backup file is missing playlist metadata or tracks")
    for item in backup["tracks"]:
        if not isinstance(item, dict) or not isinstance(item.get("spotify_uri"), str):
            raise ValueError("backup contains an invalid track entry")
    return backup
