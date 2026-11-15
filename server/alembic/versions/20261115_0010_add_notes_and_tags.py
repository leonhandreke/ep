"""add note, note_link, tag and entity_tag

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-15

Full-text search over notes: ``ep_note_text(body)`` is the text of a note with each
link ``[[type:uuid|Display text]]`` as its display text (escapes undone; a link without
display text drops out), so that UUIDs are not indexed and snippets read as prose. (It
knows no Markdown: unlike ``ep.notes.links``, it also rewrites links in code.)
``ep_notes`` is the ``simple`` configuration (no stemming: notes mix languages and
names) with ``unaccent`` for words with non-ASCII letters, so "bjork" finds "Björk"
and ``ts_headline`` still highlights the original text. (Matching and ``/search`` use
``ep_normalize``, not ``unaccent``, migration 0004: they need a Python twin; text search
needs a dictionary, and ``unaccent`` is the one Postgres bundles.)
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EP_NOTE_TEXT = r"""
CREATE FUNCTION ep_note_text(body text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
RETURN regexp_replace(
    regexp_replace(
        body,
        '\[\[[a-z_]+:[0-9A-Fa-f-]{36}(?:\|((?:\\.|[^\\\]\n])*))?\]\]',
        '\1',
        'g'
    ),
    '\\([\\|\[\]])',
    '\1',
    'g'
)
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(EP_NOTE_TEXT)
    op.execute("CREATE TEXT SEARCH CONFIGURATION ep_notes (COPY = simple)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION ep_notes "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple"
    )
    op.create_table(
        "note",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum(
                "artist",
                "release_group",
                "recording",
                "radio_show",
                "radio_episode",
                name="entitytype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note")),
        sa.UniqueConstraint("entity_type", "entity_id", name=op.f("uq_note_entity_type_entity_id")),
    )
    op.create_index(
        "ix_note_search",
        "note",
        [sa.literal_column("to_tsvector('ep_notes'::regconfig, ep_note_text(body))")],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "tag",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tag")),
    )
    op.create_index("uq_tag_name_lower", "tag", [sa.literal_column("lower(name)")], unique=True)
    op.create_table(
        "entity_tag",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum(
                "artist",
                "release_group",
                "recording",
                "radio_show",
                "radio_episode",
                name="entitytype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tag.id"], name=op.f("fk_entity_tag_tag_id_tag"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_tag")),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "tag_id",
            name=op.f("uq_entity_tag_entity_type_entity_id_tag_id"),
        ),
    )
    op.create_index(op.f("ix_entity_tag_tag_id"), "entity_tag", ["tag_id"], unique=False)
    op.create_table(
        "note_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "artist",
                "release_group",
                "recording",
                "radio_show",
                "radio_episode",
                name="entitytype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_ref", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("raw", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["note_id"], ["note.id"], name=op.f("fk_note_link_note_id_note"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_link")),
    )
    op.create_index(op.f("ix_note_link_note_id"), "note_link", ["note_id"], unique=False)
    op.create_index(
        "ix_note_link_target_type_target_id",
        "note_link",
        ["target_type", "target_id"],
        unique=False,
    )
    op.create_index(
        "ix_note_link_target_type_target_ref",
        "note_link",
        ["target_type", "target_ref"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_note_link_target_type_target_ref", table_name="note_link")
    op.drop_index("ix_note_link_target_type_target_id", table_name="note_link")
    op.drop_index(op.f("ix_note_link_note_id"), table_name="note_link")
    op.drop_table("note_link")
    op.drop_index(op.f("ix_entity_tag_tag_id"), table_name="entity_tag")
    op.drop_table("entity_tag")
    op.drop_index("uq_tag_name_lower", table_name="tag")
    op.drop_table("tag")
    op.drop_index("ix_note_search", table_name="note", postgresql_using="gin")
    op.drop_table("note")
    op.execute("DROP TEXT SEARCH CONFIGURATION ep_notes")
    op.execute("DROP FUNCTION ep_note_text(text)")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
