"""Catalogue tables: a simplified MusicBrainz model (see plans/backend.md).

Enumerations are stored as plain ``VARCHAR`` (``native_enum=False``) so that adding
a value does not need a migration of a Postgres enum type.
"""

import uuid
from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, Relationship

from ep.models.base import Base


def _enum_column(enum: type[StrEnum]) -> sa.Enum:
    return sa.Enum(
        enum, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e]
    )


class ArtistType(StrEnum):
    person = "person"
    group = "group"
    other = "other"


class ReleaseGroupType(StrEnum):
    album = "album"
    ep = "ep"
    single = "single"
    compilation = "compilation"
    other = "other"


class EntityType(StrEnum):
    """Entities that external IDs (and later notes and tags) can attach to."""

    artist = "artist"
    release_group = "release_group"
    recording = "recording"


class Artist(Base, table=True):
    mbid: uuid.UUID | None = Field(default=None, unique=True)
    name: str
    sort_name: str
    disambiguation: str = ""
    type: ArtistType | None = Field(default=None, sa_type=_enum_column(ArtistType))


class ArtistCredit(Base, table=True):
    """An ordered list of credited artists, shared by every entity with the same credit.

    Credits are never edited in place: use ``ep.credits.get_or_create_artist_credit``.
    """

    __tablename__ = "artist_credit"

    # Rendered credit string, e.g. "Simon & Garfunkel".
    name: str

    names: list[ArtistCreditName] = Relationship(
        back_populates="artist_credit",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "ArtistCreditName.position"},
    )


class ArtistCreditName(Base, table=True):
    __tablename__ = "artist_credit_name"
    __table_args__ = (sa.UniqueConstraint("artist_credit_id", "position"),)

    artist_credit_id: uuid.UUID = Field(foreign_key="artist_credit.id", ondelete="CASCADE")
    artist_id: uuid.UUID = Field(foreign_key="artist.id")
    position: int
    credited_name: str
    join_phrase: str = ""

    artist_credit: ArtistCredit = Relationship(back_populates="names")
    artist: Artist = Relationship()


class ReleaseGroup(Base, table=True):
    __tablename__ = "release_group"

    mbid: uuid.UUID | None = Field(default=None, unique=True)
    # MBID of the one release we keep for this release group.
    release_mbid: uuid.UUID | None = Field(default=None, unique=True)
    title: str
    artist_credit_id: uuid.UUID = Field(foreign_key="artist_credit.id")
    type: ReleaseGroupType = Field(sa_type=_enum_column(ReleaseGroupType))
    # Partial date as text: YYYY, YYYY-MM or YYYY-MM-DD.
    date: str | None = None
    cover_path: str | None = None
    barcode: str | None = None

    artist_credit: ArtistCredit = Relationship()
    tracks: list[Track] = Relationship(
        back_populates="release_group",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "[Track.disc, Track.position]"},
    )


class Recording(Base, table=True):
    mbid: uuid.UUID | None = Field(default=None, unique=True)
    title: str
    artist_credit_id: uuid.UUID = Field(foreign_key="artist_credit.id")
    length_ms: int | None = None

    artist_credit: ArtistCredit = Relationship()
    tracks: list[Track] = Relationship(back_populates="recording")


class Track(Base, table=True):
    __table_args__ = (sa.UniqueConstraint("release_group_id", "disc", "position"),)

    release_group_id: uuid.UUID = Field(foreign_key="release_group.id", ondelete="CASCADE")
    recording_id: uuid.UUID = Field(foreign_key="recording.id")
    disc: int = 1
    position: int
    # Overrides the recording title on this release group when set.
    title: str | None = None

    release_group: ReleaseGroup = Relationship(back_populates="tracks")
    recording: Recording = Relationship(back_populates="tracks")


class ExternalId(Base, table=True):
    """An identifier in another system (ISRC, Bandcamp URL, Deezer ID, ...).

    ``entity_id`` points at the row of ``entity_type``; there is no foreign key, so
    deleting an entity must delete its external IDs (``ep.api.external_ids``).
    """

    __tablename__ = "external_id"
    __table_args__ = (sa.UniqueConstraint("entity_type", "entity_id", "scheme", "value"),)

    entity_type: EntityType = Field(sa_type=_enum_column(EntityType))
    entity_id: uuid.UUID
    scheme: str
    value: str
