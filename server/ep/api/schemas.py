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
    AwareDatetime,
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
    RadioStation,
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


# Radio


class RadioShowUpdate(BaseModel):
    name: NonEmptyStr | MISSING = MISSING
    description: str | MISSING = MISSING


class RadioShowRead(Read):
    station: RadioStation
    slug: str
    name: str
    description: str
    image_path: str | None = Field(exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        """Path of the show's image (``GET /covers/radio-show/{id}``), if there is one."""
        return f"/covers/radio-show/{self.id}" if self.image_path else None


class RadioEpisodeUpdate(BaseModel):
    name: NonEmptyStr | MISSING = MISSING
    description: str | MISSING = MISSING
    broadcast_at: AwareDatetime | MISSING | None = MISSING
    location: NonEmptyStr | MISSING | None = MISSING


class RadioEpisodeRead(Read):
    show_id: uuid.UUID
    slug: str
    name: str
    description: str
    broadcast_at: datetime | None
    location: str | None
    # The episode page.
    url: str
    # The episode's audio file (a ``file`` source); null if it was not downloaded.
    source_id: uuid.UUID | None
    image_path: str | None = Field(exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        """Path of the episode's image (``GET /covers/radio-episode/{id}``), if any."""
        return f"/covers/radio-episode/{self.id}" if self.image_path else None


class RadioEpisodeTrackRead(Read):
    """A tracklist entry: the station's text, the time range within the episode, and the
    recording it was matched to."""

    episode_id: uuid.UUID
    position: int
    # As given by the station.
    artist_text: str
    title_text: str
    # Within the episode (the slice ``source_id``); null if the station gave no time.
    start_ms: int | None
    end_ms: int | None
    offset_is_estimate: bool
    # The slice of the episode file that plays the entry.
    source_id: uuid.UUID | None
    # The matched recording; null while unmatched.
    recording: RecordingRead | None
    # Whether the library has the recording other than from radio (a file, or a slice
    # of an album file), and how to play that.
    in_library: bool
    library_playback: PlaybackRead | None
    # How to play the entry within the episode.
    playback: PlaybackRead | None


class RadioEpisodeDetail(RadioEpisodeRead):
    show: RadioShowRead
    # How to play the whole episode.
    playback: PlaybackRead | None
    # Length of the episode file, if known (the scrubber's scale; the last entry may
    # have no end).
    duration_ms: int | None
    tracks: list[RadioEpisodeTrackRead]


class RadioEpisodeTrackUpdate(BaseModel):
    # Another recording, or null: not matched.
    recording_id: uuid.UUID | MISSING | None = MISSING
    # Within the episode. Changing the range clears ``offset_is_estimate`` unless given.
    start_ms: NonNegativeInt | MISSING = MISSING
    end_ms: PositiveInt | MISSING | None = MISSING
    offset_is_estimate: bool | MISSING = MISSING


class RadioAppearanceRead(BaseModel):
    """A recording played in a radio episode."""

    # The tracklist entry.
    track_id: uuid.UUID
    position: int
    start_ms: int | None
    end_ms: int | None
    offset_is_estimate: bool
    recording: RecordingRead
    episode: RadioEpisodeRead
    show: RadioShowRead


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


class EntityRead(BaseModel):
    """An artist, release group, recording, radio show or radio episode, as shown in
    lists of mixed types (search, link chips, backlinks, tags)."""

    type: EntityType
    id: uuid.UUID
    # Null for radio shows and episodes.
    mbid: uuid.UUID | None
    # Artist name, release group / recording title, or show / episode name.
    title: str
    # Artist disambiguation, the artist credit of a release group / recording, the
    # station of a show, the show and date of an episode.
    subtitle: str | None
    # A release group's cover; for a recording, the cover of a release group it is on;
    # a show's or episode's image.
    cover_url: str | None
    # For writing a link to it: the UUID to use (the MBID if there is one, else the
    # id), and the whole link with the title as display text.
    link_ref: uuid.UUID
    link: str


class SearchHitRead(EntityRead):
    """A search hit."""

    # Higher is better; only comparable within one response.
    score: float


class ResolvedRead(BaseModel):
    type: EntityType
    id: uuid.UUID


# Notes, links, backlinks, tags


# How every text range in the API counts (field descriptions in the OpenAPI schema).
UTF16 = "UTF-16 code units, as NSString and NSRange count"


class TextRange(BaseModel):
    """A range of a text in UTF-16 code units (as NSString and NSRange count; an emoji
    outside the Basic Multilingual Plane counts 2), ``end`` exclusive. Every text range
    in the API is one of these."""

    start: NonNegativeInt = Field(description=f"Offset of the first character, in {UTF16}.")
    end: NonNegativeInt = Field(description=f"Offset after the last character, in {UTF16}.")


class NoteLinkRead(BaseModel):
    """A link in a note body, ``[[type:uuid|Display text]]``."""

    range: TextRange = Field(
        description=f"Where the whole ``[[...]]`` is in the note body, in {UTF16}."
    )
    # The link as written.
    raw: str
    target_type: EntityType
    # The UUID in the link (an MBID or an internal ID).
    ref: uuid.UUID
    # The display text (unescaped); null if the link has none.
    display_text: str | None
    # The entity it points at now; null if the link is broken (nothing has the UUID,
    # or the target was deleted).
    target: EntityRead | None


class NoteRead(BaseModel):
    """The note of an entity. An entity without a note has an empty one (``body`` "",
    ``updated_at`` null)."""

    entity_type: EntityType
    # The entity's internal ID.
    entity_id: uuid.UUID
    # Markdown.
    body: str
    # Send this back with the next save (``NoteWrite.base_updated_at``).
    updated_at: datetime | None
    links: list[NoteLinkRead]


class NoteWrite(BaseModel):
    # Markdown; empty (or blank) deletes the note.
    body: str
    # ``updated_at`` of the note this edit started from (null or omitted: from an empty
    # note; millisecond precision is enough). If the note changed since, the save is a
    # 409 with the current note.
    base_updated_at: AwareDatetime | None = None


class NoteConflictRead(BaseModel):
    """The note was changed (by another client) since the edit started."""

    detail: str
    current: NoteRead


class SnippetRead(BaseModel):
    """A piece of a note's text (links shown as their display text), with the parts to
    highlight."""

    text: str
    highlights: list[TextRange] = Field(
        description=f"The parts of ``text`` to highlight (offsets into ``text``, in {UTF16})."
    )


class BacklinkRead(BaseModel):
    """A note that links to the entity."""

    # The entity whose note it is.
    source: EntityRead
    note_updated_at: datetime
    # The line around each link to the entity, in the order of the note.
    snippets: list[SnippetRead]


class NoteSearchHitRead(BaseModel):
    """A note matching a full-text search."""

    # The entity whose note it is.
    source: EntityRead
    note_updated_at: datetime
    snippet: SnippetRead
    # Higher is better; only comparable within one response.
    rank: float


TagName = Annotated[
    str,
    AfterValidator(lambda name: " ".join(name.split())),
    StringConstraints(min_length=1, max_length=100),
]
"""A tag name: trimmed, inner whitespace collapsed."""


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    # How many entities have it.
    count: int


class TagAdd(BaseModel):
    # Found ignoring case, created if new.
    name: TagName


class TagUpdate(BaseModel):
    # A name another tag has (ignoring case) merges this tag into that one.
    name: TagName


class EntityDetailsRead(BaseModel):
    """What the detail panel shows for an entity, in one request: the same reads as
    ``GET /notes``, ``/entity-tags``, ``/backlinks`` and ``/…/radio-appearances``."""

    entity: EntityRead
    note: NoteRead
    tags: list[TagRead]
    backlinks: list[BacklinkRead]
    # For a recording, the episodes that played it; for an artist, the episodes that
    # played anything credited to them; empty for other types.
    radio_appearances: list[RadioAppearanceRead]


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
    # ``folder``: an absolute directory path under the media roots; ``bandcamp``: an
    # album or track page URL; ``youtube``: a video or playlist URL; ``nts``: an
    # episode page URL.
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


# Watched directories


class WatchDirCreate(BaseModel):
    # Absolute directory path under the media roots, e.g. /watch/Library.
    path: NonEmptyStr
    enabled: bool = True


class WatchDirUpdate(BaseModel):
    enabled: bool | MISSING = MISSING


class WatchDirRead(Read):
    path: str
    enabled: bool
    # Folders that last changed before this are imported only by an explicit scan.
    baseline_at: datetime


class WatchDirScan(BaseModel):
    # A directory inside the watch dir to limit the scan to; null scans all of it.
    path: NonEmptyStr | None = None
