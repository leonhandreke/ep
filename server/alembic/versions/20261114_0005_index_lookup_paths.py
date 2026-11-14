"""indexes for lookup paths

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14

Foreign keys and columns that queries filter on, which no index or unique constraint
covered yet: a recording's sources (playback), a source's derived sources, an artist's
credits (release groups and recordings of an artist), a credit by its rendered name
(reusing a credit).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_artist_credit_by_name", "artist_credit", ["name"])
    op.create_index("ix_artist_credit_name_artist_id", "artist_credit_name", ["artist_id"])
    op.create_index("ix_source_parent_id", "source", ["parent_id"])
    op.create_index("ix_source_recording_id", "source", ["recording_id"])


def downgrade() -> None:
    op.drop_index("ix_source_recording_id", table_name="source")
    op.drop_index("ix_source_parent_id", table_name="source")
    op.drop_index("ix_artist_credit_name_artist_id", table_name="artist_credit_name")
    op.drop_index("ix_artist_credit_by_name", table_name="artist_credit")
