"""add source table

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "file",
                "slice",
                "youtube",
                "bandcamp",
                "soundcloud",
                "nts",
                "spotify",
                "torrent",
                name="sourcekind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("uri", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("recording_id", sa.Uuid(), nullable=True),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("codec", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["source.id"], name=op.f("fk_source_parent_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recording.id"], name=op.f("fk_source_recording_id_recording")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
        sa.UniqueConstraint("kind", "uri", name=op.f("uq_source_kind_uri")),
    )


def downgrade() -> None:
    op.drop_table("source")
