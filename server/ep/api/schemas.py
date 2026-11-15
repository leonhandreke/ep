"""Request and response schemas.

Create schemas list required fields without defaults. Update (PATCH) schemas default
every field to ``MISSING``: omitted fields are left alone, and only nullable columns
accept an explicit ``null``.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    MISSING,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    computed_field,
)

from ep.importers.proposal import Choice, LogEntry, Proposal
from ep.models import (
    ArtistType,
    EntityType,
    ImportKind,
    JobStage,
    JobState,
    ReleaseGroupType,
    SourceKind,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _check_partial_date(value: str) -> str:
    year, month, day = (int(part) for part in (*value.split("-"), "1", "1")[:3])
    date(year, month, day)  # raises ValueError for an impossible month or day
    return value


PartialDate = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$"),
    AfterValidator(_check_partial_date),
]
"""A date as ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``."""


class Page(BaseModel):
    """Limit/offset pagination for list endpoints."""

    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class Read(BaseModel):
    # Fields with defaults (``playback``) are still always present in responses: mark
    # them required in the OpenAPI schema, for the generated client.
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# Artist credits


class ArtistCreditNameIn(BaseModel):
    artist_id: uuid.UUID
    # Defaults to the artist's name.
    credited_name: NonEmptyStr | None = None
    join_phrase: str = ""


ArtistCreditIn = Annotated[list[ArtistCreditNameIn], Field(min_length=1)]


class ArtistCreditNameRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artist_id: uuid.UUID
    credited_name: str
    join_phrase: str


class ArtistCreditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    names: list[ArtistCreditNameRead]


# Artists


class ArtistCreate(BaseModel):
    mbid: uuid.UUID | None = None
    name: NonEmptyStr
    # Defaults to the name.
    sort_name: NonEmptyStr | None = None
    disambiguation: str = ""
    type: ArtistType | None = None


class ArtistUpdate(BaseModel):
    mbid: uuid.UUID | MISSING | None = MISSING
    name: NonEmptyStr | MISSING = MISSING
    sort_name: NonEmptyStr | MISSING = MISSING
    disambiguation: str | MISSING = MISSING
    type: ArtistType | MISSING | None = MISSING


class ArtistRead(Read):
    mbid: uuid.UUID | None
    name: str
    sort_name: str
    disambiguation: str
    type: ArtistType | None


# Release groups


class ReleaseGroupCreate(BaseModel):
    mbid: uuid.UUID | None = None
    release_mbid: uuid.UUID | None = None
    title: NonEmptyStr
    artist_credit: ArtistCreditIn
    type: ReleaseGroupType = ReleaseGroupType.album
    date: PartialDate | None = None
    barcode: NonEmptyStr | None = None


class ReleaseGroupUpdate(BaseModel):
    mbid: uuid.UUID | MISSING | None = MISSING
    release_mbid: uuid.UUID | MISSING | None = MISSING
    title: NonEmptyStr | MISSING = MISSING
    artist_credit: ArtistCreditIn | MISSING = MISSING
    type: ReleaseGroupType | MISSING = MISSING
    date: PartialDate | MISSING | None = MISSING
    barcode: NonEmptyStr | MISSING | None = MISSING


class ReleaseGroupRead(Read):
    mbid: uuid.UUID | None
    release_mbid: uuid.UUID | None
    title: str
    artist_credit: ArtistCreditRead
    type: ReleaseGroupType
    date: str | None
    barcode: str | None
    cover_path: str | None = Field(exclude=True)

    @computed_field
    @property
    def cover_url(self) -> str | None:
        """Path of the cover image (``GET /covers/release-group/{id}``), if there is one."""
        return f"/covers/release-group/{self.id}" if self.cover_path else None


# Recordings


class RecordingCreate(BaseModel):
    mbid: uuid.UUID | None = None
    title: NonEmptyStr
    artist_credit: ArtistCreditIn
    length_ms: NonNegativeInt | None = None


class RecordingUpdate(BaseModel):
    mbid: uuid.UUID | MISSING | None = MISSING
    title: NonEmptyStr | MISSING = MISSING
    artist_credit: ArtistCreditIn | MISSING = MISSING
    length_ms: NonNegativeInt | MISSING | None = MISSING


class RecordingRead(Read):
    mbid: uuid.UUID | None
    title: str
    artist_credit: ArtistCreditRead
    length_ms: int | None


class RecordingTrackRead(Read):
    """A track of a recording, seen from the recording: with its release group."""

    disc: int
    position: int
    title: str | None
    release_group: ReleaseGroupRead


class RecordingDetail(RecordingRead):
    tracks: list[RecordingTrackRead]


# Tracks


class TrackCreate(BaseModel):
    release_group_id: uuid.UUID
    recording_id: uuid.UUID
    disc: PositiveInt = 1
    position: NonNegativeInt
    # Overrides the recording title on this release group.
    title: NonEmptyStr | None = None


class TrackUpdate(BaseModel):
    recording_id: uuid.UUID | MISSING = MISSING
    disc: PositiveInt | MISSING = MISSING
    position: NonNegativeInt | MISSING = MISSING
    title: NonEmptyStr | MISSING | None = MISSING


class PlaybackRead(BaseModel):
    """How to play a source: stream ``file_source_id`` and play ``start_ms`` to ``end_ms``.

    ``GET /sources/{id}/stream`` serves the whole file for any source that resolves to
    it; the client seeks. ``end_ms`` null means to the end of the file.
    """

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    file_source_id: uuid.UUID
    start_ms: int
    end_ms: int | None


class TrackRead(Read):
    release_group_id: uuid.UUID
    disc: int
    position: int
    title: str | None
    recording: RecordingRead
    # The recording's first playable source; null if it has none.
    playback: PlaybackRead | None = None


# External IDs


class ExternalIdCreate(BaseModel):
    # e.g. isrc, deezer, spotify, bandcamp, youtube, nts, acr
    scheme: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    value: NonEmptyStr


class ExternalIdRead(Read):
    entity_type: EntityType
    entity_id: uuid.UUID
    scheme: str
    value: str


# Sources

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SourceCreate(BaseModel):
    kind: SourceKind
    # Absolute path for file (must exist), URL/URI for remote kinds, omitted for slice.
    uri: NonEmptyStr | None = None
    parent_id: uuid.UUID | None = None
    # Slice only: range within the parent. No end: to the end of the parent.
    start_ms: NonNegativeInt | None = None
    end_ms: PositiveInt | None = None
    recording_id: uuid.UUID | None = None
    managed: bool = False
    # For file sources, codec/duration_ms/size are read from the file when omitted.
    codec: NonEmptyStr | None = None
    duration_ms: NonNegativeInt | None = None
    size: NonNegativeInt | None = None
    sha256: Sha256 | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    kind: SourceKind | MISSING = MISSING
    uri: NonEmptyStr | MISSING | None = MISSING
    parent_id: uuid.UUID | MISSING | None = MISSING
    start_ms: NonNegativeInt | MISSING | None = MISSING
    end_ms: PositiveInt | MISSING | None = MISSING
    recording_id: uuid.UUID | MISSING | None = MISSING
    managed: bool | MISSING = MISSING
    codec: NonEmptyStr | MISSING | None = MISSING
    duration_ms: NonNegativeInt | MISSING | None = MISSING
    size: NonNegativeInt | MISSING | None = MISSING
    sha256: Sha256 | MISSING | None = MISSING
    meta: dict[str, Any] | MISSING = MISSING


class SourceRead(Read):
    kind: SourceKind
    uri: str | None
    parent_id: uuid.UUID | None
    start_ms: int | None
    end_ms: int | None
    recording_id: uuid.UUID | None
    managed: bool
    codec: str | None
    duration_ms: int | None
    size: int | None
    sha256: str | None
    meta: dict[str, Any]
    # Null if the source does not resolve to a file (e.g. a remote-only link).
    playback: PlaybackRead | None = None


# Search and resolve


class SearchHitRead(BaseModel):
    """A search hit: an artist, release group or recording."""

    type: EntityType
    id: uuid.UUID
    mbid: uuid.UUID | None
    # Artist name, or release group / recording title.
    title: str
    # Artist disambiguation, or the artist credit of a release group / recording.
    subtitle: str | None
    # A release group's cover; for a recording, the cover of a release group it is on.
    cover_url: str | None
    # Higher is better; only comparable within one response.
    score: float


class ResolvedRead(BaseModel):
    type: EntityType
    id: uuid.UUID


# MusicBrainz


class MusicBrainzSearchType(StrEnum):
    artist = "artist"
    release = "release"
    recording = "recording"


class MusicBrainzHitRead(BaseModel):
    type: MusicBrainzSearchType
    mbid: uuid.UUID
    # Artist name, or release / recording title.
    title: str
    # The artist credit of a release or recording; null for artists.
    artist_credit: str | None
    disambiguation: str
    # Release date, or a recording's first release date.
    date: str | None
    # MusicBrainz relevance, 0-100.
    score: int | None


class MusicBrainzImport(BaseModel):
    release_mbid: uuid.UUID


# Imports (the proposal and issue structure is ``ep.importers.proposal``)


class ImportCreate(BaseModel):
    kind: ImportKind
    # For ``folder``: an absolute directory path under the media roots.
    input: NonEmptyStr


class JobAction(StrEnum):
    """What the user can do with a job now."""

    # Answer its open issues: ``POST /imports/{id}/review``.
    review = "review"
    # Run it again from the start: ``POST /imports/{id}/retry``.
    retry = "retry"
    # Remove it from the inbox: ``POST /imports/{id}/dismiss``.
    dismiss = "dismiss"


class ProgressRead(BaseModel):
    # The stage the job is in (or stopped in); null before it starts.
    stage: JobStage | None
    # Items of this stage done, out of ``total`` (null if not known yet).
    done: int
    total: int | None


class ImportJobSummary(Read):
    kind: ImportKind
    input: str
    state: JobState
    progress: ProgressRead
    error: str | None
    # Issues waiting for an answer.
    open_issues: int
    actions: list[JobAction]


class ImportJobRead(ImportJobSummary):
    log: list[LogEntry]
    # What applying will do (or did), with every issue raised and its answer; null
    # before the job has run (or if it failed before building one).
    proposal: Proposal | None


class IssueAnswer(BaseModel):
    # ``Issue.id`` of one of the job's issues.
    issue_id: NonEmptyStr
    # One of the issue's ``choices``.
    choice: Choice
    # Required for ``pick_candidate``: the ``id`` of one of the issue's candidates.
    candidate_id: uuid.UUID | None = None


class ReviewRequest(BaseModel):
    answers: Annotated[list[IssueAnswer], Field(min_length=1)]

