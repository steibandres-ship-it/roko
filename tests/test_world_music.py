from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.world_music.catalog import import_catalog_batch
from app.world_music.db import get_session
from app.world_music.identity import IdentityConflict
from app.world_music.models import Artist, Base, IngestionBatch, Market, Track
from app.world_music.providers.registry import ProviderRegistry
from app.world_music.schemas import CatalogImportInput
from app.world_music.api import app


def sample_batch(**overrides) -> CatalogImportInput:
    payload = {
        "provenance": {
            "provider_name": "Synthetic unit-test fixture",
            "rights_basis": "owned",
            "captured_at": "2026-01-01T12:00:00Z",
            "data_confidence": 0.8,
        },
        "markets": [
            {"key": "WORLD", "name": "World", "scope": "world"},
            {"key": "CL", "name": "Chile", "scope": "country", "iso_code": "cl", "parent_key": "WORLD"},
        ],
        "genres": [
            {"slug": "latin-urban", "name": "Latin Urban", "level": "family"},
            {"slug": "reggaeton", "name": "Reggaeton", "level": "genre", "parent_slug": "latin-urban"},
        ],
        "artists": [
            {
                "key": "artist-one",
                "name": "Synthetic Test Artist",
                "country_code": "cl",
                "external_identifiers": [{"provider": "MusicBrainz", "identifier": "artist-001"}],
                "genre_slugs": ["reggaeton"],
            }
        ],
        "releases": [
            {
                "key": "release-one",
                "title": "Synthetic Test Release",
                "release_date": "2026-01-01",
                "external_identifiers": [{"provider": "MusicBrainz", "identifier": "release-001"}],
            }
        ],
        "tracks": [
            {
                "key": "track-one",
                "title": "Synthetic Test Track",
                "isrc": "QZ9AB2600001",
                "release_ref": "release-one",
                "artist_refs": ["artist-one"],
                "languages": ["es"],
                "genre_weights": [{"genre_slug": "reggaeton", "weight": 1.0}],
                "market_keys": ["CL"],
            }
        ],
    }
    payload.update(overrides)
    return CatalogImportInput.model_validate(payload)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_catalog_import_is_idempotent_and_resolves_track_by_isrc(session_factory):
    batch = sample_batch()
    with session_factory() as session:
        first = import_catalog_batch(session, batch)
        assert first["duplicate_batch"] is False
        second = import_catalog_batch(session, batch)
        assert second["duplicate_batch"] is True
        assert session.scalar(select(func.count()).select_from(Track)) == 1
        assert session.scalar(select(func.count()).select_from(Artist)) == 1

        changed_provenance = batch.model_copy(deep=True)
        changed_provenance.provenance = changed_provenance.provenance.model_copy(
            update={"captured_at": datetime(2026, 1, 2, tzinfo=timezone.utc)}
        )
        third = import_catalog_batch(session, changed_provenance)
        assert third["duplicate_batch"] is False
        assert session.scalar(select(func.count()).select_from(Track)) == 1
        assert session.scalar(select(func.count()).select_from(Artist)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionBatch)) == 2


def test_isrc_conflict_does_not_merge_different_titles(session_factory):
    with session_factory() as session:
        import_catalog_batch(session, sample_batch())
        conflicting = sample_batch()
        conflicting.tracks[0].title = "Different Recording Title"
        conflicting.provenance = conflicting.provenance.model_copy(
            update={"captured_at": datetime(2026, 1, 3, tzinfo=timezone.utc)}
        )
        with pytest.raises(IdentityConflict):
            import_catalog_batch(session, conflicting)
        assert session.scalar(select(func.count()).select_from(Track)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionBatch)) == 1


def test_market_codes_and_weighted_genres_are_validated():
    with pytest.raises(ValidationError):
        sample_batch(markets=[{"key": "XX", "name": "Unknown", "scope": "country", "iso_code": "XX"}])
    invalid = sample_batch().model_dump(mode="json")
    invalid["tracks"][0]["genre_weights"][0]["weight"] = 0.7
    with pytest.raises(ValidationError):
        CatalogImportInput.model_validate(invalid)


def test_disconnected_providers_do_not_claim_a_successful_refresh():
    statuses = ProviderRegistry().statuses()
    assert statuses
    assert {item.state for item in statuses} == {"PROVIDER_NOT_CONNECTED"}
    assert all(item.last_refresh is None for item in statuses)


def test_api_health_overview_and_market_genres(session_factory):
    factory = session_factory

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async def check_routes():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                health_response = await client.get("/api/health")
                overview_response = await client.get("/api/world/overview")
                empty_markets = await client.get("/api/markets")
                with factory() as session:
                    import_catalog_batch(session, sample_batch())
                market_response = await client.get("/api/markets/cl")
                genres_response = await client.get("/api/markets/CL/genres")
                missing_market = await client.get("/api/markets/US")
                return (
                    health_response,
                    overview_response,
                    empty_markets,
                    market_response,
                    genres_response,
                    missing_market,
                )

        health_response, overview_response, empty_markets, market, genres, missing_market = asyncio.run(check_routes())
        assert health_response.json()["database"] == "ok"
        assert overview_response.json()["tracks_monitored"] == 0
        assert overview_response.json()["trend_scores_available"] == 0
        assert empty_markets.json() == []
        assert market.status_code == 200
        assert market.json()["iso_code"] == "CL"
        assert genres.json() == [{"slug": "reggaeton", "name": "Reggaeton", "catalog_track_count": 1}]
        assert missing_market.status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
