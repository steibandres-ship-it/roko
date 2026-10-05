from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import Engine, URL, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATA_DIR, PROJECT_ROOT
from .models import Base


def database_url() -> URL | str:
    values = dict(dotenv_values(PROJECT_ROOT / ".env"))
    values.update(os.environ)
    configured = str(values.get("WORLD_MUSIC_DATABASE_URL") or "").strip()
    if configured:
        return configured
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return URL.create("sqlite", database=str(DATA_DIR / "world_music_os.sqlite3"))


def make_engine(url: URL | str | None = None, **kwargs) -> Engine:
    resolved = url if url is not None else database_url()
    parsed = make_url(resolved) if isinstance(resolved, str) else resolved
    if parsed.drivername.startswith("sqlite"):
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    return create_engine(parsed, pool_pre_ping=True, **kwargs)


engine = make_engine()
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
