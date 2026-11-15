"""Radio: shows, episodes and their tracklists (see plans/backend.md, "Radio").

Kept apart from the catalogue: a tracklist entry keeps the station's raw artist and
title text, and points at the recording it was matched to (``recording_id``, null while
unmatched) and at the ``slice`` source that plays it within the episode file
(``source_id``, null if the station gave no time for it). Recordings only known from
radio tracklists are hidden from browse lists by default (``ep.radio``).

Show and episode names have generated ``*_normalized`` columns with trigram indexes,
like the catalogue's titles, for ``/search``.
"""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, Relationship

from ep.models.base import Base
from ep.models.catalogue import Recording, enum_column, normalized, trigram_index
from ep.models.source import Source


class RadioStation(StrEnum):
    nts = "nts"


class RadioShow(Base, table=True):
    __tablename__ = "radio_show"
    __table_args__ = (
        sa.UniqueConstraint("station", "slug"),
        trigram_index("radio_show", "name_normalized"),
    )

    station: RadioStation = Field(sa_type=enum_column(RadioStation))
    # The station's identifier for the show (NTS: the show alias in its URL).
    slug: str
    name: str
    name_normalized: str = normalized("name")
    description: str = ""
    image_path: str | None = None

    episodes: list[RadioEpisode] = Relationship(
        back_populates="show",
        sa_relationship_kwargs={"order_by": "[RadioEpisode.broadcast_at.desc(), RadioEpisode.id]"},
    )


class RadioEpisode(Base, table=True):
    __tablename__ = "radio_episode"
    __table_args__ = (
        # Also serves "the episodes of a show".
        sa.UniqueConstraint("show_id", "slug"),
        trigram_index("radio_episode", "name_normalized"),
    )

    show_id: uuid.UUID = Field(foreign_key="radio_show.id")
    # The station's identifier for the episode (NTS: the episode alias in its URL).
    slug: str
    name: str
    name_normalized: str = normalized("name")
    description: str = ""
    broadcast_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    location: str | None = None
    image_path: str | None = None
    # The episode's audio file (a ``file`` source; the tracklist's slices point into it).
    source_id: uuid.UUID | None = Field(default=None, foreign_key="source.id")
    # The episode page.
    url: str = Field(unique=True)

    show: RadioShow = Relationship(back_populates="episodes")
    source: Source | None = Relationship()
    tracks: list[RadioEpisodeTrack] = Relationship(
        back_populates="episode",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "RadioEpisodeTrack.position"},
    )


class RadioEpisodeTrack(Base, table=True):
    __tablename__ = "radio_episode_track"
    __table_args__ = (sa.UniqueConstraint("episode_id", "position"),)

    episode_id: uuid.UUID = Field(foreign_key="radio_episode.id", ondelete="CASCADE")
    position: int
    # As given by the station.
    artist_text: str
    title_text: str
    # The matched recording; null while unmatched. Indexed: a recording's radio
    # appearances, "is this recording radio-only".
    recording_id: uuid.UUID | None = Field(default=None, foreign_key="recording.id", index=True)
    # The slice of the episode file that plays it. Indexed: "is this source a radio slice".
    source_id: uuid.UUID | None = Field(default=None, foreign_key="source.id", index=True)
    # The time range is the station's estimate, not a detected position.
    offset_is_estimate: bool = False

    episode: RadioEpisode = Relationship(back_populates="tracks")
    recording: Recording | None = Relationship()
    source: Source | None = Relationship()
