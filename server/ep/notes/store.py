"""Saving notes (and rebuilding their links), and what deleting an entity must do.

Links are resolved twice: on save, ``note_link.target_id`` is the entity the UUID named
then; on read (``GET /notes``, backlinks), the UUID is looked up again, so a link
written before its target existed, or with an internal ID of an entity that gained an
MBID since, still resolves. The user's text is never rewritten.
"""

import uuid

from sqlalchemy import ColumnElement, exists, or_, update
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col, delete, select

from ep.entities import resolve_refs
from ep.models import EntityTag, EntityType, Note, NoteLink
from ep.notes.links import parse_links


def get_note(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, lock: bool = False
) -> Note | None:
    query = select(Note).where(Note.entity_type == entity_type, Note.entity_id == entity_id)
    if lock:
        query = query.with_for_update()
    return session.exec(query).first()


def save_note(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID, body: str
) -> Note | None:
    """Set the note of an entity and rebuild its links from the body; an empty (or
    blank) body deletes the note. Returns the note (None if deleted). Flushes."""
    note = get_note(session, entity_type, entity_id, lock=True)
    if not body.strip():
        if note is not None:
            session.delete(note)
            session.flush()
        return None
    if note is None:
        note = Note(entity_type=entity_type, entity_id=entity_id, body=body)
        session.add(note)
    else:
        note.body = body
    links = parse_links(body)
    resolved = resolve_refs(session, [(link.type, link.ref) for link in links], summary=False)
    note.links = [
        NoteLink(
            note_id=note.id,
            target_type=link.type,
            target_ref=link.ref,
            target_id=target.id if (target := resolved.get((link.type, link.ref))) else None,
            raw=link.raw,
            start=link.start,
            end=link.end,
        )
        for link in links
    ]
    session.flush()
    return note


def links_to(entity_type: EntityType, id: uuid.UUID, refs: list[uuid.UUID]) -> ColumnElement[bool]:
    """SQL: a ``note_link`` points at this entity (saved so, or by one of its UUIDs:
    its ID, MBID, release MBID)."""
    return (col(NoteLink.target_type) == entity_type) & or_(
        col(NoteLink.target_id) == id, col(NoteLink.target_ref).in_(refs)
    )


def annotated(
    entity_type: EntityType,
    id: Mapped[uuid.UUID] | ColumnElement[uuid.UUID],
    mbid: Mapped[uuid.UUID | None] | ColumnElement[uuid.UUID | None] | None = None,
) -> ColumnElement[bool]:
    """SQL: the entity with this id (a column of the enclosing query) has a note, a tag
    or an inbound link. Such an entity is never deleted as a side effect (of deleting
    an episode, re-importing, re-pointing a tracklist entry)."""
    inbound = or_(col(NoteLink.target_id) == id, col(NoteLink.target_ref) == id)
    if mbid is not None:
        inbound = or_(inbound, col(NoteLink.target_ref) == mbid)
    return or_(
        exists().where(col(Note.entity_type) == entity_type, col(Note.entity_id) == id),
        exists().where(col(EntityTag.entity_type) == entity_type, col(EntityTag.entity_id) == id),
        exists().where(col(NoteLink.target_type) == entity_type, inbound),
    )


def delete_annotations(session: Session, entity_type: EntityType, entity_id: uuid.UUID) -> None:
    """Delete the note and the tags of an entity, and mark links to it unresolved
    (``target_id`` null; the linking notes' text stays). Call this when deleting the
    entity (with ``ep.external_ids.delete_external_ids``)."""
    session.exec(
        delete(Note).where(col(Note.entity_type) == entity_type, col(Note.entity_id) == entity_id)
    )
    session.exec(
        delete(EntityTag).where(
            col(EntityTag.entity_type) == entity_type, col(EntityTag.entity_id) == entity_id
        )
    )
    session.exec(
        update(NoteLink)
        .where(col(NoteLink.target_type) == entity_type, col(NoteLink.target_id) == entity_id)
        .values(target_id=None)
    )
