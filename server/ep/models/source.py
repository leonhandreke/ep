"""Sources: anything that locates audio (see plans/backend.md, "Sources").

A source may be derived from another (``parent_id``): a downloaded file from a
YouTube link, a slice (time range) of a whole-album file. Playback walks up to the
nearest ``file`` source (``ep.playback``).
"""

import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship

from ep.models.base import Base
from ep.models.catalogue import enum_column


class SourceKind(StrEnum):
    file = "file"
    slice = "slice"
    youtube = "youtube"
    bandcamp = "bandcamp"
    soundcloud = "soundcloud"
    nts = "nts"
    spotify = "spotify"
    torrent = "torrent"


class Source(Base, table=True):
    # One row per location: a path is indexed once, a URL linked once.
    __table_args__ = (sa.UniqueConstraint("kind", "uri"),)

    kind: SourceKind = Field(sa_type=enum_column(SourceKind))
    # Absolute path (as seen by the server) for ``file``; URL or URI for remote kinds;
    # null for ``slice``.
    uri: str | None = None
    # The source this one was obtained from. Indexed: derived sources of a source.
    parent_id: uuid.UUID | None = Field(default=None, foreign_key="source.id", index=True)
    # Time range within the parent (``slice`` only). No end: to the end of the parent.
    start_ms: int | None = None
    end_ms: int | None = None
    # The recording this source plays; null for containers (whole-album or episode file).
    # Indexed: a recording's sources (playback).
    recording_id: uuid.UUID | None = Field(default=None, foreign_key="recording.id", index=True)
    # True if EP downloaded it into the managed library; false for files indexed in place.
    managed: bool = False
    # A ``file``'s container as a media type (``ep.probe.media_type``: audio/mpeg,
    # audio/ogg, audio/webm, ...) and its audio codec as ffprobe names it (mp3, aac,
    # flac, opus, vorbis, ...).
    media_type: str | None = None
    codec: str | None = None
    duration_ms: int | None = None
    size: int | None = Field(default=None, sa_type=sa.BigInteger)
    sha256: str | None = None
    # Stream facts of a ``file`` (``ep.probe``): sample rate (Hz), bits per sample
    # (lossless formats), bit rate (bit/s), channel count.
    sample_rate: int | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    channels: int | None = None
    # Extractor leftovers (yt-dlp, tags, ...).
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)

    # Given as a SQLAlchemy relationship: SQLModel cannot resolve ``Source | None`` itself.
    parent: Source | None = Relationship(
        sa_relationship=relationship("Source", remote_side="Source.id", back_populates="children")
    )
    children: list[Source] = Relationship(back_populates="parent")
