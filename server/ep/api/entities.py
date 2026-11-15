"""Entities of mixed types as ``EntityRead`` (search hits, link targets, backlinks,
tagged entities), and finding an entity named in a path by MBID or ID."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class RecordingAlbum:
    """For a recording's summary: the (oldest) release group it is on, and a cover."""

    title: str | None = None
    cover_url: str | None = None


def recording_albums(
    session: Session, recording_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, RecordingAlbum]:
    """For each recording on a release group: the oldest one's title, and the cover of
    the oldest one with a cover (one query)."""
    ids = set(recording_ids)
    albums: dict[uuid.UUID, RecordingAlbum] = {}
    if not ids:
        return albums
    rows = session.exec(
        select(Track.recording_id, ReleaseGroup.id, ReleaseGroup.title, ReleaseGroup.cover_path)
        .join(ReleaseGroup, col(Track.release_group_id) == ReleaseGroup.id)
        .where(col(Track.recording_id).in_(ids))
        .order_by(col(ReleaseGroup.date).asc().nulls_last(), col(ReleaseGroup.id))
    )
    for recording_id, release_group_id, title, cover_path in rows:
        album = albums.get(recording_id) or RecordingAlbum(title=title)
        if album.cover_url is None and cover_path:
            album = RecordingAlbum(album.title, _release_group_cover(release_group_id))
        albums[recording_id] = album
    return albums


def entity_read(entity: Base, albums: dict[uuid.UUID, RecordingAlbum]) -> EntityRead:
    """``albums``: of recordings (``recording_albums``). Release groups and
    recordings need their artist credit loaded, episodes their show
    (``ep.entities.SUMMARY_OPTIONS``)."""
    release_group_type = date = length = album_title = None
    match entity:
        case Artist():
            title, subtitle, cover = entity.name, entity.disambiguation or None, None
        case ReleaseGroup():
            title, subtitle = entity.title, entity.artist_credit.name
            cover = _release_group_cover(entity.id) if entity.cover_path else None
            release_group_type, date = entity.type, entity.date
        case Recording():
            album = albums.get(entity.id, RecordingAlbum())
            title, subtitle, cover = entity.title, entity.artist_credit.name, album.cover_url
            length, album_title = entity.length_ms, album.title
        case RadioShow():
            title, subtitle = entity.name, entity.station.upper()
            cover = f"/covers/radio-show/{entity.id}" if entity.image_path else None
        case RadioEpisode():
            title, subtitle = entity.name, entity.show.name
            if entity.broadcast_at is not None:
                date = f"{entity.broadcast_at:%Y-%m-%d}"
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
        release_group_type=release_group_type,
        date=date,
        length_ms=length,
        release_group_title=album_title,
        link_ref=ref,
        link=format_link(entity_type, ref, title),
    )


def entity_reads(session: Session, entities: Iterable[Base]) -> dict[uuid.UUID, EntityRead]:
    """``EntityRead`` of each entity, by ID (one query for recordings' release groups)."""
    entities = list(entities)
    albums = recording_albums(session, (e.id for e in entities if isinstance(e, Recording)))
    return {entity.id: entity_read(entity, albums) for entity in entities}
