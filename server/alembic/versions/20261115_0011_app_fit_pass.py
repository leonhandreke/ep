"""app fit pass: stream facts, preferred source, job origin, progress and results

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_job", sa.Column("progress_item", sa.Float(), nullable=True))
    op.add_column("import_job", sa.Column("watch_dir_id", sa.Uuid(), nullable=True))
    op.add_column(
        "import_job",
        sa.Column(
            "created",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_index("ix_import_job_watch_dir_id", "import_job", ["watch_dir_id"], unique=False)
    op.create_foreign_key(
        "fk_import_job_watch_dir_id_watch_dir",
        "import_job",
        "watch_dir",
        ["watch_dir_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("recording", sa.Column("preferred_source_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_recording_preferred_source_id_source",
        "recording",
        "source",
        ["preferred_source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in "sample_rate", "bit_depth", "bit_rate", "channels":
        op.add_column("source", sa.Column(column, sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in "channels", "bit_rate", "bit_depth", "sample_rate":
        op.drop_column("source", column)
    op.drop_constraint("fk_recording_preferred_source_id_source", "recording", type_="foreignkey")
    op.drop_column("recording", "preferred_source_id")
    op.drop_constraint("fk_import_job_watch_dir_id_watch_dir", "import_job", type_="foreignkey")
    op.drop_index("ix_import_job_watch_dir_id", table_name="import_job")
    op.drop_column("import_job", "created")
    op.drop_column("import_job", "watch_dir_id")
    op.drop_column("import_job", "progress_item")
