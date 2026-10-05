from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .identity import IdentityConflict, add_external_identifiers, find_by_external_identifiers, resolve_track
from .models import (
    Artist,
    ArtistGenre,
    Genre,
    IngestionBatch,
    Market,
    Release,
    Track,
    TrackArtist,
    TrackGenre,
    TrackLanguage,
    TrackMarket,
)
from .schemas import CatalogImportInput, ExternalIdentifierInput


def _digest(batch: CatalogImportInput) -> str:
    canonical = json.dumps(batch.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_existing_name(existing: str, incoming: str, entity_type: str) -> None:
    if " ".join(existing.casefold().split()) != " ".join(incoming.casefold().split()):
        raise IdentityConflict(f"identified {entity_type} has conflicting names; review the source records before merging")


def _get_or_create_market(session: Session, row: Any, parent: Market | None) -> Market:
    existing = session.scalar(select(Market).where(Market.market_key == row.key))
    if existing:
        _check_existing_name(existing.name, row.name, "market")
        if existing.parent_id and parent and existing.parent_id != parent.id:
            raise IdentityConflict("market is already assigned to a different parent")
        existing.parent_id = existing.parent_id or (parent.id if parent else None)
        return existing
    market = Market(market_key=row.key, name=row.name, scope=row.scope, iso_code=row.iso_code, parent=parent)
    session.add(market)
    session.flush()
    return market


def _get_or_create_genre(session: Session, row: Any) -> Genre:
    existing = session.scalar(select(Genre).where(Genre.slug == row.slug))
    if existing:
        _check_existing_name(existing.name, row.name, "genre")
        if row.parent_slug:
            parent = session.scalar(select(Genre).where(Genre.slug == row.parent_slug))
            if not parent:
                raise ValueError("genre parent must be imported before its child genre")
            if existing.parent_id and existing.parent_id != parent.id:
                raise IdentityConflict("genre is already assigned to a different parent")
            existing.parent_id = existing.parent_id or parent.id
        return existing
    if row.parent_slug:
        parent = session.scalar(select(Genre).where(Genre.slug == row.parent_slug))
        if not parent:
            raise ValueError("genre parent must be imported before its child genre in version 0.1")
    else:
        parent = None
    genre = Genre(slug=row.slug, name=row.name, level=row.level, parent=parent)
    session.add(genre)
    session.flush()
    return genre


def _get_or_create_artist(session: Session, row: Any, batch_id: str) -> Artist:
    existing = find_by_external_identifiers(session, "artist", row.external_identifiers)
    if existing:
        _check_existing_name(existing.name, row.name, "artist")
        if existing.country_code is None:
            existing.country_code = row.country_code
        existing.source_batch_id = batch_id
        artist = existing
    else:
        artist = Artist(name=row.name, country_code=row.country_code, source_batch_id=batch_id)
        session.add(artist)
        session.flush()
    add_external_identifiers(session, "artist", artist.id, row.external_identifiers)
    return artist


def _get_or_create_release(session: Session, row: Any, batch_id: str) -> Release:
    existing = find_by_external_identifiers(session, "release", row.external_identifiers)
    if existing:
        _check_existing_name(existing.title, row.title, "release")
        existing.release_date = existing.release_date or row.release_date
        existing.source_batch_id = batch_id
        release = existing
    else:
        release = Release(title=row.title, release_date=row.release_date, source_batch_id=batch_id)
        session.add(release)
        session.flush()
    add_external_identifiers(session, "release", release.id, row.external_identifiers)
    return release


def _get_or_create_track(session: Session, row: Any, batch_id: str, release: Release | None) -> Track:
    existing = resolve_track(session, row.isrc, row.external_identifiers)
    if existing:
        _check_existing_name(existing.title, row.title, "track")
        if existing.isrc and row.isrc and existing.isrc != row.isrc:
            raise IdentityConflict("identified track has conflicting ISRCs")
        existing.isrc = existing.isrc or row.isrc
        existing.release_id = existing.release_id or (release.id if release else None)
        existing.source_batch_id = batch_id
        track = existing
    else:
        track = Track(isrc=row.isrc, title=row.title, release_id=release.id if release else None, source_batch_id=batch_id)
        session.add(track)
        session.flush()
    add_external_identifiers(session, "track", track.id, row.external_identifiers)
    return track


def import_catalog_batch(session: Session, batch: CatalogImportInput) -> dict[str, Any]:
    content_hash = _digest(batch)
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        duplicate = session.scalar(select(IngestionBatch).where(IngestionBatch.content_hash == content_hash))
        if duplicate:
            return {
                "batch_id": duplicate.id,
                "duplicate_batch": True,
                "artists": duplicate.artists_imported,
                "releases": duplicate.releases_imported,
                "tracks": duplicate.tracks_imported,
            }

        provenance = batch.provenance
        captured_at = provenance.captured_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        source = IngestionBatch(
            provider_name=provenance.provider_name,
            rights_basis=provenance.rights_basis,
            source_url=provenance.source_url,
            captured_at=captured_at,
            data_confidence=provenance.data_confidence,
            content_hash=content_hash,
            artists_imported=len(batch.artists),
            releases_imported=len(batch.releases),
            tracks_imported=len(batch.tracks),
        )
        session.add(source)
        session.flush()

        markets: dict[str, Market] = {}
        for row in batch.markets:
            parent = markets.get(row.parent_key) if row.parent_key else None
            if row.parent_key:
                if parent is None:
                    raise ValueError("markets must be listed parent-first in an import batch")
            market = _get_or_create_market(session, row, parent)
            markets[row.key] = market

        genres: dict[str, Genre] = {}
        for row in batch.genres:
            genre = _get_or_create_genre(session, row)
            if row.parent_slug:
                if row.parent_slug not in genres:
                    raise ValueError("genres must be listed parent-first in an import batch")
                genre.parent_id = genres[row.parent_slug].id
            genres[row.slug] = genre

        artists: dict[str, Artist] = {}
        for row in batch.artists:
            artist = _get_or_create_artist(session, row, source.id)
            artists[row.key] = artist
            linked_genres = set(
                session.scalars(select(ArtistGenre.genre_id).where(ArtistGenre.artist_id == artist.id)).all()
            )
            for slug in row.genre_slugs:
                genre = genres[slug]
                if genre.id not in linked_genres:
                    session.add(ArtistGenre(artist_id=artist.id, genre_id=genre.id))
                    linked_genres.add(genre.id)

        releases: dict[str, Release] = {}
        for row in batch.releases:
            releases[row.key] = _get_or_create_release(session, row, source.id)

        for row in batch.tracks:
            release = releases.get(row.release_ref) if row.release_ref else None
            track = _get_or_create_track(session, row, source.id, release)
            current_artist_ids = set(
                session.scalars(select(TrackArtist.artist_id).where(TrackArtist.track_id == track.id)).all()
            )
            for position, artist_key in enumerate(row.artist_refs):
                artist = artists[artist_key]
                if artist.id not in current_artist_ids:
                    role = "primary" if position == 0 else "featured"
                    session.add(TrackArtist(track_id=track.id, artist_id=artist.id, position=position, role=role))
                    current_artist_ids.add(artist.id)

            current_languages = set(
                session.scalars(select(TrackLanguage.language_code).where(TrackLanguage.track_id == track.id)).all()
            )
            for language in row.languages:
                normalized_language = language.strip().casefold()
                if normalized_language and normalized_language not in current_languages:
                    session.add(TrackLanguage(track_id=track.id, language_code=normalized_language))
                    current_languages.add(normalized_language)

            current_genres = {
                relation.genre_id: relation
                for relation in session.scalars(select(TrackGenre).where(TrackGenre.track_id == track.id)).all()
            }
            for item in row.genre_weights:
                genre = genres[item.genre_slug]
                if genre.id not in current_genres:
                    session.add(
                        TrackGenre(
                            track_id=track.id,
                            genre_id=genre.id,
                            weight=item.weight,
                            data_confidence=provenance.data_confidence,
                            source_batch_id=source.id,
                        )
                    )
                elif provenance.data_confidence >= current_genres[genre.id].data_confidence:
                    current_genres[genre.id].weight = item.weight
                    current_genres[genre.id].data_confidence = provenance.data_confidence
                    current_genres[genre.id].source_batch_id = source.id

            current_markets = {
                relation.market_id: relation
                for relation in session.scalars(select(TrackMarket).where(TrackMarket.track_id == track.id)).all()
            }
            for market_key in row.market_keys:
                market = markets[market_key]
                if market.id not in current_markets:
                    session.add(
                        TrackMarket(
                            track_id=track.id,
                            market_id=market.id,
                            data_confidence=provenance.data_confidence,
                            source_batch_id=source.id,
                        )
                    )

        session.flush()
        return {
            "batch_id": source.id,
            "duplicate_batch": False,
            "artists": len(artists),
            "releases": len(releases),
            "tracks": len(batch.tracks),
        }
