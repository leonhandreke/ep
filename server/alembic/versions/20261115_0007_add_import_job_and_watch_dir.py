"""add import_job and watch_dir

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "folder",
                "bandcamp",
                "youtube",
                "nts",
                name="importkind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("input", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "queued",
                "running",
                "needs_review",
                "applying",
                "done",
                "failed",
                "dismissed",
                name="jobstate",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "fetch",
                "resolve",
                "match",
                "propose",
                "download",
                "apply",
                name="jobstage",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("progress_done", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("log", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_job")),
    )
    op.create_index("ix_import_job_kind_input", "import_job", ["kind", "input"])
    op.create_index("ix_import_job_state", "import_job", ["state"])
    op.create_table(
        "watch_dir",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_dir")),
        sa.UniqueConstraint("path", name=op.f("uq_watch_dir_path")),
    )


def downgrade() -> None:
    op.drop_table("watch_dir")
    op.drop_index("ix_import_job_state", table_name="import_job")
    op.drop_index("ix_import_job_kind_input", table_name="import_job")
    op.drop_table("import_job")
