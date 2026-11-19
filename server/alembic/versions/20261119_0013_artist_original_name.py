"""artist original name: the name in its own script when the name is a Latin alias

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artist", sa.Column("original_name", sa.String(), nullable=True))
    op.add_column(
        "artist",
        sa.Column(
            "original_name_normalized",
            sa.Text(),
            sa.Computed("ep_normalize(original_name)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_artist_original_name_normalized_trgm",
        "artist",
        ["original_name_normalized"],
        postgresql_using="gin",
        postgresql_ops={"original_name_normalized": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # Dropping the column drops its index.
    op.drop_column("artist", "original_name_normalized")
    op.drop_column("artist", "original_name")
