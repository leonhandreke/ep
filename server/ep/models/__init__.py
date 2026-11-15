"""SQLModel tables.

Import every table module here so that ``SQLModel.metadata`` (used by Alembic
autogenerate) sees all tables.
"""

from ep.models.base import Base, rel
from ep.models.catalogue import (
    Artist,
    ArtistCredit,
    ArtistCreditName,
    ArtistType,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Track,
)
from ep.models.notes import NOTE_SEARCH_CONFIG, EntityTag, Note, NoteLink, Tag, note_document
from ep.models.operations import ImportJob, ImportKind, JobStage, JobState, WatchDir
from ep.models.radio import RadioEpisode, RadioEpisodeTrack, RadioShow, RadioStation
from ep.models.source import Source, SourceKind

__all__ = [
    "NOTE_SEARCH_CONFIG",
    "Artist",
    "ArtistCredit",
    "ArtistCreditName",
    "ArtistType",
    "Base",
    "EntityTag",
    "EntityType",
    "ExternalId",
    "ImportJob",
    "ImportKind",
    "JobStage",
    "JobState",
    "Note",
    "NoteLink",
    "RadioEpisode",
    "RadioEpisodeTrack",
    "RadioShow",
    "RadioStation",
    "Recording",
    "ReleaseGroup",
    "ReleaseGroupType",
    "Source",
    "SourceKind",
    "Tag",
    "Track",
    "WatchDir",
    "note_document",
    "rel",
]
