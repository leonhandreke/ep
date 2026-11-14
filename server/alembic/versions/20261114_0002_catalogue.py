"""add catalogue tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mbid", sa.Uuid(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sort_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("disambiguation", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("person", "group", "other", name="artisttype", native_enum=False, length=32),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist")),
        sa.UniqueConstraint("mbid", name=op.f("uq_artist_mbid")),
    )
    op.create_table(
        "artist_credit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist_credit")),
    )
    op.create_table(
        "external_id",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum(
                "artist",
                "release_group",
                "recording",
                name="entitytype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("value", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_id")),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "scheme",
            "value",
            name=op.f("uq_external_id_entity_type_entity_id_scheme_value"),
        ),
    )
    op.create_table(
        "artist_credit_name",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artist_credit_id", sa.Uuid(), nullable=False),
        sa.Column("artist_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("credited_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("join_phrase", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artist_credit_id"],
            ["artist_credit.id"],
            name=op.f("fk_artist_credit_name_artist_credit_id_artist_credit"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artist.id"], name=op.f("fk_artist_credit_name_artist_id_artist")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist_credit_name")),
        sa.UniqueConstraint(
            "artist_credit_id",
            "position",
            name=op.f("uq_artist_credit_name_artist_credit_id_position"),
        ),
    )
    op.create_table(
        "recording",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mbid", sa.Uuid(), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("artist_credit_id", sa.Uuid(), nullable=False),
        sa.Column("length_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_credit_id"],
            ["artist_credit.id"],
            name=op.f("fk_recording_artist_credit_id_artist_credit"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recording")),
        sa.UniqueConstraint("mbid", name=op.f("uq_recording_mbid")),
    )
    op.create_table(
        "release_group",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mbid", sa.Uuid(), nullable=True),
        sa.Column("release_mbid", sa.Uuid(), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("artist_credit_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "album",
                "ep",
                "single",
                "compilation",
                "other",
                name="releasegrouptype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("date", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cover_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("barcode", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artist_credit_id"],
            ["artist_credit.id"],
            name=op.f("fk_release_group_artist_credit_id_artist_credit"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_group")),
        sa.UniqueConstraint("mbid", name=op.f("uq_release_group_mbid")),
        sa.UniqueConstraint("release_mbid", name=op.f("uq_release_group_release_mbid")),
    )
    op.create_table(
        "track",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_group_id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("disc", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recording.id"], name=op.f("fk_track_recording_id_recording")
        ),
        sa.ForeignKeyConstraint(
            ["release_group_id"],
            ["release_group.id"],
            name=op.f("fk_track_release_group_id_release_group"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track")),
        sa.UniqueConstraint(
            "release_group_id",
            "disc",
            "position",
            name=op.f("uq_track_release_group_id_disc_position"),
        ),
    )


def downgrade() -> None:
    op.drop_table("track")
    op.drop_table("release_group")
    op.drop_table("recording")
    op.drop_table("artist_credit_name")
    op.drop_table("external_id")
    op.drop_table("artist_credit")
    op.drop_table("artist")
