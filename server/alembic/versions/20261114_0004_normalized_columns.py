"""normalised columns and indexes for matching and search

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14

``ep_normalize(text)`` is the SQL twin of ``ep.matching.normalize.normalize`` (keep
them in step; ``tests/test_normalize_sql.py`` checks that they agree): case folding
(Unicode full case folding of the builtin ``pg_unicode_fast`` collation, independent of
the OS locale), a few letters that do not decompose, compatibility decomposition, no
combining diacritical marks, recomposition, "&" as "and", non-word characters as single
spaces. It is IMMUTABLE, as stored generated columns require. It replaces ``unaccent``,
whose rules file Python cannot reproduce, so that extension is dropped.

Names and titles get a stored ``*_normalized`` column with a GIN trigram index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EP_NORMALIZE = r"""
CREATE FUNCTION ep_normalize(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
RETURN btrim(
    regexp_replace(
        replace(
            normalize(
                regexp_replace(
                    normalize(
                        replace(
                            replace(
                                replace(
                                    translate(
                                        casefold(value COLLATE pg_unicode_fast),
                                        'øđðłħı',
                                        'oddlhi'
                                    ),
                                    'æ', 'ae'
                                ),
                                'œ', 'oe'
                            ),
                            'þ', 'th'
                        ),
                        NFKD
                    ),
                    '[̀-ͯ]+', '', 'g'
                ),
                NFC
            ),
            '&', ' and '
        ),
        '[\W_]+', ' ', 'g'
    )
)
"""

# (table, source column): each gets a generated "<column>_normalized" with a trigram index.
NORMALIZED = [
    ("artist", "name"),
    ("artist", "sort_name"),
    ("artist_credit", "name"),
    ("release_group", "title"),
    ("recording", "title"),
]


def upgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute(EP_NORMALIZE)
    for table, column in NORMALIZED:
        normalized = f"{column}_normalized"
        op.add_column(
            table,
            sa.Column(
                normalized,
                sa.Text(),
                sa.Computed(f"ep_normalize({column})", persisted=True),
                nullable=False,
            ),
        )
        op.create_index(
            f"ix_{table}_{normalized}_trgm",
            table,
            [normalized],
            postgresql_using="gin",
            postgresql_ops={normalized: "gin_trgm_ops"},
        )
    op.create_index("ix_release_group_artist_credit_id", "release_group", ["artist_credit_id"])
    op.create_index("ix_recording_artist_credit_id", "recording", ["artist_credit_id"])
    op.create_index("ix_track_recording_id", "track", ["recording_id"])
    op.create_index(
        "ix_release_group_barcode", "release_group", [sa.func.ltrim(sa.column("barcode"), "0")]
    )
    op.create_index("ix_external_id_scheme_value", "external_id", ["scheme", "value"])


def downgrade() -> None:
    op.drop_index("ix_external_id_scheme_value", table_name="external_id")
    op.drop_index("ix_release_group_barcode", table_name="release_group")
    op.drop_index("ix_track_recording_id", table_name="track")
    op.drop_index("ix_recording_artist_credit_id", table_name="recording")
    op.drop_index("ix_release_group_artist_credit_id", table_name="release_group")
    for table, column in reversed(NORMALIZED):
        # Dropping the column drops its index.
        op.drop_column(table, f"{column}_normalized")
    op.execute("DROP FUNCTION ep_normalize(text)")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
