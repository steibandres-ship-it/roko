from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class JsonStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, value: Any, *, private: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            if private and os.name != "nt":
                os.chmod(temp_name, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.path)
            if private and os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class PlaylistRegistry:
    def __init__(self, path: Path | None = None):
        self.store = JsonStore(path or DATA_DIR / "playlist_registry.json")

    def all(self) -> dict[str, dict[str, Any]]:
        return self.store.read({})

    def get(self, slug: str) -> dict[str, Any] | None:
        return self.all().get(slug)

    def update(self, slug: str, fields: dict[str, Any]) -> None:
        data = self.all()
        current = data.get(slug, {})
        current.update(fields)
        data[slug] = current
        self.store.write(data)


def append_unresolved(entries: list[dict[str, Any]], path: Path | None = None) -> None:
    JsonStore(path or DATA_DIR / "unresolved_tracks.json").write(entries)


def append_history(entry: dict[str, Any], path: Path | None = None) -> None:
    store = JsonStore(path or DATA_DIR / "sync_history.json")
    history = store.read([])
    history.append(entry)
    store.write(history[-500:])
