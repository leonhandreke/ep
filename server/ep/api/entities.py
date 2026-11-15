"""Entities of mixed types as ``EntityRead`` (search hits, link targets, backlinks,
tagged entities), and finding an entity named in a path by MBID or ID."""

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from ep.api.schemas import EntityRead
from ep.entities import entity_type_of, link_ref, mbid_of, resolve_ref
from ep.models import (
    Artist,
    Base,
    EntityType,
    RadioEpisode,
    RadioShow,
    Recording,
    ReleaseGroup,
    Track,
)
from ep.notes.links import format_link


def get_entity_or_404(session: Session, entity_type: EntityType, ref: uuid.UUID) -> Base:
    """The entity a path names by MBID or internal ID (``/notes/{type}/{id}``, ...)."""
    if (entity := resolve_ref(session, entity_type, ref, summary=True)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} {ref} not found")
    return entity


def _release_group_cover(id: uuid.UUID) -> str:
    return f"/covers/release-group/{id}"


def recording_covers(session: Session, recording_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """A cover URL for each recording that is on a release group with a cover (the
    oldest such release group), in one query."""
    ids = set(recording_ids)
    covers: dict[uuid.UUID, str] = {}
    if not ids:
        return covers
    rows = session.exec(
        select(Track.recording_id, ReleaseGroup.id)
        .join(ReleaseGroup, col(Track.release_group_id) == ReleaseGroup.id)
        .where(col(Track.recording_id).in_(ids), col(ReleaseGroup.cover_path).is_not(None))
        .order_by(col(ReleaseGroup.date).asc().nulls_last(), col(ReleaseGroup.id))
    )
    for recording_id, release_group_id in rows:
        covers.setdefault(recording_id, _release_group_cover(release_group_id))
    return covers


def entity_read(entity: Base, covers: dict[uuid.UUID, str]) -> EntityRead:
    """``covers``: recording covers (``recording_covers``). Release groups and
    recordings need their artist credit loaded, episodes their show
    (``ep.entities.SUMMARY_OPTIONS``)."""
    match entity:
        case Artist():
            title, subtitle, cover = entity.name, entity.disambiguation or None, None
        case ReleaseGroup():
            title, subtitle = entity.title, entity.artist_credit.name
            cover = _release_group_cover(entity.id) if entity.cover_path else None
        case Recording():
            title, subtitle, cover = entity.title, entity.artist_credit.name, covers.get(entity.id)
        case RadioShow():
            title, subtitle = entity.name, entity.station.upper()
            cover = f"/covers/radio-show/{entity.id}" if entity.image_path else None
        case RadioEpisode():
            title, subtitle = entity.name, entity.show.name
            if entity.broadcast_at is not None:
                subtitle += f", {entity.broadcast_at:%Y-%m-%d}"
            cover = f"/covers/radio-episode/{entity.id}" if entity.image_path else None
        case _:
            raise TypeError(f"unexpected entity {entity!r}")
    entity_type = entity_type_of(entity)
    ref = link_ref(entity)
    return EntityRead(
        type=entity_type,
        id=entity.id,
        mbid=mbid_of(entity),
        title=title,
        subtitle=subtitle,
        cover_url=cover,
        link_ref=ref,
        link=format_link(entity_type, ref, title),
    )


def entity_reads(session: Session, entities: Iterable[Base]) -> dict[uuid.UUID, EntityRead]:
    """``EntityRead`` of each entity, by ID (one query for recording covers)."""
    entities = list(entities)
    covers = recording_covers(session, (e.id for e in entities if isinstance(e, Recording)))
    return {entity.id: entity_read(entity, covers) for entity in entities}
