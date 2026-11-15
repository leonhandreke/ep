"""The entity types notes, tags and links attach to, and finding them by MBID or ID.

A link (``[[type:uuid|...]]``, ``/resolve/{type}/{uuid}``, ``/notes/{type}/{uuid}``)
names an entity by its MBID if it has one, else by its internal ID; a release group
is also found by the MBID of its release. Radio shows and episodes have internal IDs
only.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.interfaces import ORMOption
from sqlmodel import Session, col, select

from ep.models import (
    Artist,
    Base,
    EntityType,
    RadioEpisode,
    RadioShow,
    Recording,
    ReleaseGroup,
    rel,
)

MODELS: dict[EntityType, type[Base]] = {
    EntityType.artist: Artist,
    EntityType.release_group: ReleaseGroup,
    EntityType.recording: Recording,
    EntityType.radio_show: RadioShow,
    EntityType.radio_episode: RadioEpisode,
}
TYPES: dict[type[Base], EntityType] = {model: type for type, model in MODELS.items()}

# What a summary of the entity (title, subtitle, cover) needs loaded.
SUMMARY_OPTIONS: dict[EntityType, tuple[ORMOption, ...]] = {
    EntityType.release_group: (joinedload(rel(ReleaseGroup.artist_credit)),),
    EntityType.recording: (joinedload(rel(Recording.artist_credit)),),
    EntityType.radio_episode: (joinedload(rel(RadioEpisode.show)),),
}


def entity_type_of(entity: Base) -> EntityType:
    return TYPES[type(entity)]


def mbid_of(entity: Base) -> uuid.UUID | None:
    return getattr(entity, "mbid", None)


def title_of(entity: Base) -> str:
    """Artist name, release group / recording title, show / episode name."""
    match entity:
        case Artist() | RadioShow() | RadioEpisode():
            return entity.name
        case ReleaseGroup() | Recording():
            return entity.title
    raise TypeError(f"unexpected entity {entity!r}")


def link_ref(entity: Base) -> uuid.UUID:
    """The UUID a link to the entity uses: its MBID if it has one, else its ID."""
    return mbid_of(entity) or entity.id


def ref_columns(entity_type: EntityType) -> list[Any]:
    """The columns a link UUID is looked up in."""
    match entity_type:
        case EntityType.artist:
            return [col(Artist.id), col(Artist.mbid)]
        case EntityType.release_group:
            return [col(ReleaseGroup.id), col(ReleaseGroup.mbid), col(ReleaseGroup.release_mbid)]
        case EntityType.recording:
            return [col(Recording.id), col(Recording.mbid)]
        case EntityType.radio_show:
            return [col(RadioShow.id)]
        case EntityType.radio_episode:
            return [col(RadioEpisode.id)]


def refers_to(entity: Base, ref: uuid.UUID) -> bool:
    """Whether a link UUID names this entity."""
    return ref in (entity.id, mbid_of(entity), getattr(entity, "release_mbid", None))


def resolve_refs(
    session: Session, refs: Iterable[tuple[EntityType, uuid.UUID]], summary: bool = True
) -> dict[tuple[EntityType, uuid.UUID], Base]:
    """The entities the link UUIDs name, one query per entity type (loaded for a summary
    unless ``summary`` is false). UUIDs that name nothing are left out."""
    by_type: dict[EntityType, set[uuid.UUID]] = defaultdict(set)
    for entity_type, ref in refs:
        by_type[entity_type].add(ref)
    found: dict[tuple[EntityType, uuid.UUID], Base] = {}
    for entity_type, uuids in by_type.items():
        condition: ColumnElement[bool] = or_(
            *(column.in_(uuids) for column in ref_columns(entity_type))
        )
        query = select(MODELS[entity_type]).where(condition)
        if summary:
            query = query.options(*SUMMARY_OPTIONS.get(entity_type, ()))
        entities = session.exec(query).all()
        for ref in uuids:
            # An internal ID wins over an MBID (they never collide in practice).
            matches = sorted((e for e in entities if refers_to(e, ref)), key=lambda e: e.id != ref)
            if matches:
                found[entity_type, ref] = matches[0]
    return found


def resolve_ref(
    session: Session, entity_type: EntityType, ref: uuid.UUID, summary: bool = False
) -> Base | None:
    """The entity a link UUID names, or None."""
    return resolve_refs(session, [(entity_type, ref)], summary).get((entity_type, ref))


def load_entities(
    session: Session, keys: Iterable[tuple[EntityType, uuid.UUID]]
) -> dict[tuple[EntityType, uuid.UUID], Base]:
    """Entities by internal ID, loaded for a summary, one query per entity type.
    Missing ones are left out."""
    by_type: dict[EntityType, set[uuid.UUID]] = defaultdict(set)
    for entity_type, id in keys:
        by_type[entity_type].add(id)
    found: dict[tuple[EntityType, uuid.UUID], Base] = {}
    for entity_type, ids in by_type.items():
        model = MODELS[entity_type]
        query = (
            select(model)
            .where(col(model.id).in_(ids))
            .options(*SUMMARY_OPTIONS.get(entity_type, ()))
        )
        for entity in session.exec(query):
            found[entity_type, entity.id] = entity
    return found
