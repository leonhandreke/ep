"""Notes, the links in them, and tags (see plans/backend.md, "Notes, links, tags").

Notes and tags attach to an entity by ``entity_type`` + ``entity_id``, like external
IDs: there is no foreign key, so every path that deletes an entity calls
``ep.notes.delete_annotations``. A note on a track belongs to its recording.

``note_link`` is derived from the note body (``ep.notes.links``) and rebuilt on every
save: the link's target type, the UUID as written (``target_ref``: an MBID or an
internal ID), the internal ID it resolved to when the note was saved (``target_id``;
null if nothing had that UUID, or the target was deleted since) and the raw link text.

Notes are searched with Postgres full-text search: an expression GIN index on
``to_tsvector('ep_notes', ep_note_text(body))`` (migration 0010: ``ep_note_text`` shows
links as their display text, the ``ep_notes`` configuration is ``simple`` with
``unaccent``). Tag names are unique ignoring case (an index on ``lower(name)``).
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func
from sqlmodel import Field, Relationship

from ep.models.base import Base
from ep.models.catalogue import EntityType, enum_column

# The text search configuration of notes (migration 0010).
NOTE_SEARCH_CONFIG = "ep_notes"


def note_document(body: Any) -> sa.ColumnElement[Any]:
    """The full-text document of a note body (the expression of its GIN index)."""
    return func.to_tsvector(
        sa.literal_column(f"'{NOTE_SEARCH_CONFIG}'::regconfig"), func.ep_note_text(body)
    )


class Note(Base, table=True):
    __table_args__ = (
        # Also serves "the note of this entity".
        sa.UniqueConstraint("entity_type", "entity_id"),
        sa.Index(
            "ix_note_search",
            note_document(sa.column("body")),
            postgresql_using="gin",
        ),
    )

    entity_type: EntityType = Field(sa_type=enum_column(EntityType))
    entity_id: uuid.UUID
    # Markdown, with links ``[[type:uuid|Display text]]``.
    body: str = Field(sa_type=sa.Text)

    links: list[NoteLink] = Relationship(
        back_populates="note",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "NoteLink.start"},
    )


class NoteLink(Base, table=True):
    __tablename__ = "note_link"
    __table_args__ = (
        # Backlinks: by the target saved, and by the UUID written (an MBID the target
        # gained, or a target created after the note was saved).
        sa.Index("ix_note_link_target_type_target_id", "target_type", "target_id"),
        sa.Index("ix_note_link_target_type_target_ref", "target_type", "target_ref"),
    )

    # Indexed: rebuilding a note's links.
    note_id: uuid.UUID = Field(foreign_key="note.id", ondelete="CASCADE", index=True)
    target_type: EntityType = Field(sa_type=enum_column(EntityType))
    # The UUID in the link: an MBID or an internal ID.
    target_ref: uuid.UUID
    # The internal ID it resolved to on save; null if unresolved (or the target was
    # deleted since).
    target_id: uuid.UUID | None = None
    # The link as written, e.g. ``[[artist:...|Björk]]``.
    raw: str
    # Character range of the link in the body (Unicode code points).
    start: int
    end: int

    note: Note = Relationship(back_populates="links")


class Tag(Base, table=True):
    __table_args__ = (sa.Index("uq_tag_name_lower", func.lower(sa.column("name")), unique=True),)

    # Trimmed, inner whitespace collapsed; unique ignoring case.
    name: str


class EntityTag(Base, table=True):
    __tablename__ = "entity_tag"
    __table_args__ = (
        # Also serves "the tags of this entity".
        sa.UniqueConstraint("entity_type", "entity_id", "tag_id"),
    )

    # Indexed: everything with a tag, usage counts.
    tag_id: uuid.UUID = Field(foreign_key="tag.id", ondelete="CASCADE", index=True)
    entity_type: EntityType = Field(sa_type=enum_column(EntityType))
    entity_id: uuid.UUID

    tag: Tag = Relationship()
