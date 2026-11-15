"""Tags: one name per tag (unique ignoring case), attached to entities by hand or by
imports.

Names are trimmed, with inner whitespace collapsed to one space; adding a tag by name
finds the existing tag ignoring case (the first spelling stays) or creates it.

Imported tags (Bandcamp tags and MusicBrainz genres on a release group, NTS genres and
moods on a radio episode) are attached only when the import creates the entity: an
entity that is already in the library keeps the tags it has, so re-importing never
brings back a tag the user removed, and never adds tags to rows the user already has.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import func
from sqlmodel import Session, select

from ep.models import EntityTag, EntityType, Tag

MAX_NAME_LENGTH = 100


def clean_name(name: str) -> str:
    """A tag name as stored: trimmed, whitespace runs as one space, at most
    ``MAX_NAME_LENGTH`` characters."""
    return " ".join(name.split())[:MAX_NAME_LENGTH].rstrip()


def find_tag(session: Session, name: str) -> Tag | None:
    """The tag with this name, ignoring case."""
    return session.exec(select(Tag).where(func.lower(Tag.name) == func.lower(name))).first()


def get_or_create_tag(session: Session, name: str) -> Tag:
    """The tag with this name (ignoring case), created if there is none. ``name`` must
    be clean (``clean_name``) and not empty."""
    tag = find_tag(session, name)
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


def tag_entity(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, name: str
) -> tuple[Tag, bool]:
    """Add a tag (by clean name, found or created) to an entity. Returns the tag and
    whether the entity did not have it before."""
    tag = get_or_create_tag(session, name)
    existing = session.exec(
        select(EntityTag.id).where(
            EntityTag.tag_id == tag.id,
            EntityTag.entity_type == entity_type,
            EntityTag.entity_id == entity_id,
        )
    ).first()
    if existing is not None:
        return tag, False
    session.add(EntityTag(tag_id=tag.id, entity_type=entity_type, entity_id=entity_id))
    session.flush()
    return tag, True


def add_imported_tags(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, names: Iterable[str]
) -> None:
    """Attach the tags an import brought for an entity it created (see the module
    docstring); names are cleaned, blank and duplicate ones skipped."""
    seen: set[str] = set()
    for name in names:
        name = clean_name(name)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            tag_entity(session, entity_type, entity_id, name)


def rename_tag(session: Session, tag: Tag, name: str) -> Tag:
    """Rename a tag. If another tag has the name (ignoring case), the two are merged:
    this tag's entities get the other tag (spelt as given now), and this tag is deleted.
    Returns the tag that has the name now. Flushes."""
    other = find_tag(session, name)
    if other is None or other.id == tag.id:
        tag.name = name
        session.flush()
        return tag
    tagged = select(EntityTag.entity_type, EntityTag.entity_id).where(EntityTag.tag_id == other.id)
    has_both = set(session.exec(tagged).all())
    for row in session.exec(select(EntityTag).where(EntityTag.tag_id == tag.id)).all():
        if (row.entity_type, row.entity_id) in has_both:
            session.delete(row)
        else:
            row.tag_id = other.id
    session.flush()
    session.delete(tag)
    other.name = name
    session.flush()
    return other
