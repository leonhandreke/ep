"""add radio_show, radio_episode and radio_episode_track

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "radio_show",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "station",
            sa.Enum("nts", name="radiostation", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "name_normalized",
            sa.Text(),
            sa.Computed("ep_normalize(name)", persisted=True),
            nullable=False,
        ),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("image_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radio_show")),
        sa.UniqueConstraint("station", "slug", name=op.f("uq_radio_show_station_slug")),
    )
    op.create_index(
        "ix_radio_show_name_normalized_trgm",
        "radio_show",
        ["name_normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )
    op.create_table(
        "radio_episode",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("show_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "name_normalized",
            sa.Text(),
            sa.Computed("ep_normalize(name)", persisted=True),
            nullable=False,
        ),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("broadcast_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("image_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(
            ["show_id"], ["radio_show.id"], name=op.f("fk_radio_episode_show_id_radio_show")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_radio_episode_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radio_episode")),
        sa.UniqueConstraint("show_id", "slug", name=op.f("uq_radio_episode_show_id_slug")),
        sa.UniqueConstraint("url", name=op.f("uq_radio_episode_url")),
    )
    op.create_index(
        "ix_radio_episode_name_normalized_trgm",
        "radio_episode",
        ["name_normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )
    op.create_table(
        "radio_episode_track",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("artist_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("offset_is_estimate", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["radio_episode.id"],
            name=op.f("fk_radio_episode_track_episode_id_radio_episode"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"],
            ["recording.id"],
            name=op.f("fk_radio_episode_track_recording_id_recording"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_radio_episode_track_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radio_episode_track")),
        sa.UniqueConstraint(
            "episode_id", "position", name=op.f("uq_radio_episode_track_episode_id_position")
        ),
    )
    op.create_index(
        op.f("ix_radio_episode_track_recording_id"),
        "radio_episode_track",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_radio_episode_track_source_id"), "radio_episode_track", ["source_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_radio_episode_track_source_id"), table_name="radio_episode_track")
    op.drop_index(op.f("ix_radio_episode_track_recording_id"), table_name="radio_episode_track")
    op.drop_table("radio_episode_track")
    op.drop_index(
        "ix_radio_episode_name_normalized_trgm",
        table_name="radio_episode",
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )
    op.drop_table("radio_episode")
    op.drop_index(
        "ix_radio_show_name_normalized_trgm",
        table_name="radio_show",
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )
    op.drop_table("radio_show")
