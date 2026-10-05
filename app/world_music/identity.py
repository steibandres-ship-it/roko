from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Artist, ExternalIdentifier, Release, Track
from .schemas import ExternalIdentifierInput


class IdentityConflict(ValueError):
    """An external identifier resolves to conflicting canonical entities."""


ENTITY_MODELS = {"artist": Artist, "release": Release, "track": Track}
ENTITY_FK = {"artist": "artist_id", "release": "release_id", "track": "track_id"}


def find_by_external_identifiers(
    session: Session,
    entity_type: str,
    identifiers: list[ExternalIdentifierInput],
) -> Artist | Release | Track | None:
    if entity_type not in ENTITY_MODELS:
        raise ValueError(f"unsupported entity type: {entity_type}")
    owner_field = ENTITY_FK[entity_type]
    resolved_ids: set[str] = set()
    for item in identifiers:
        row = session.scalar(
            select(ExternalIdentifier).where(
                ExternalIdentifier.entity_type == entity_type,
                ExternalIdentifier.provider == item.provider,
                ExternalIdentifier.identifier == item.identifier,
            )
        )
        if row:
            entity_id = getattr(row, owner_field)
            if entity_id:
                resolved_ids.add(entity_id)
    if len(resolved_ids) > 1:
        raise IdentityConflict(f"external identifiers resolve to multiple {entity_type} records")
    if not resolved_ids:
        return None
    return session.get(ENTITY_MODELS[entity_type], next(iter(resolved_ids)))


def add_external_identifiers(
    session: Session,
    entity_type: str,
    entity_id: str,
    identifiers: list[ExternalIdentifierInput],
) -> None:
    owner_field = ENTITY_FK[entity_type]
    for item in identifiers:
        existing = session.scalar(
            select(ExternalIdentifier).where(
                ExternalIdentifier.provider == item.provider,
                ExternalIdentifier.entity_type == entity_type,
                ExternalIdentifier.identifier == item.identifier,
            )
        )
        if existing:
            if getattr(existing, owner_field) != entity_id:
                raise IdentityConflict(
                    f"{item.provider}:{item.identifier} is already linked to a different {entity_type}"
                )
            continue
        session.add(
            ExternalIdentifier(
                entity_type=entity_type,
                provider=item.provider,
                identifier=item.identifier,
                **{owner_field: entity_id},
            )
        )


def resolve_track(
    session: Session,
    isrc: str | None,
    identifiers: list[ExternalIdentifierInput],
) -> Track | None:
    by_isrc = session.scalar(select(Track).where(Track.isrc == isrc)) if isrc else None
    by_external = find_by_external_identifiers(session, "track", identifiers) if identifiers else None
    if by_isrc and by_external and by_isrc.id != by_external.id:
        raise IdentityConflict("ISRC and external identifiers resolve to different track records")
    return by_isrc or by_external  # type: ignore[return-value]
