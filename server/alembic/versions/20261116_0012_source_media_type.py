"""source media type: a file's container, for clients to choose a decoder

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Existing files by extension (``ep.probe._EXTENSION_TYPES``); new ones are probed.
_BY_EXTENSION = {
    "aac": "audio/aac",
    "aif": "audio/aiff",
    "aiff": "audio/aiff",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "m4b": "audio/mp4",
    "mka": "audio/x-matroska",
    "mp3": "audio/mpeg",
    "mp4": "audio/mp4",
    "oga": "audio/ogg",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "webm": "audio/webm",
}


def upgrade() -> None:
    op.add_column("source", sa.Column("media_type", sa.String(), nullable=True))
    values = ", ".join(f"('{extension}', '{type}')" for extension, type in _BY_EXTENSION.items())
    op.execute(
        "UPDATE source SET media_type = types.media_type"
        f" FROM (VALUES {values}) AS types(extension, media_type)"
        " WHERE source.kind = 'file'"
        " AND lower(substring(source.uri from '\\.([^./]+)$')) = types.extension"
    )


def downgrade() -> None:
    op.drop_column("source", "media_type")
