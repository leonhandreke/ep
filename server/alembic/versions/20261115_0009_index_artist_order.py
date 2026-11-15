"""index artist browse order

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-15

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artist_sort_name_name_id", "artist", ["sort_name", "name", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_artist_sort_name_name_id", table_name="artist")
