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
from ep.models.source import Source, SourceKind

__all__ = [
    "Artist",
    "ArtistCredit",
    "ArtistCreditName",
    "ArtistType",
    "Base",
    "EntityType",
    "ExternalId",
    "Recording",
    "ReleaseGroup",
    "ReleaseGroupType",
    "Source",
    "SourceKind",
    "Track",
    "rel",
]
