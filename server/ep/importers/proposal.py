"""The proposal of an import job: what applying it will do, and what needs deciding.

Every importer (folder, Bandcamp, YouTube, NTS) describes what it found as the same
structure, stored as JSON on ``import_job.proposal`` and shown by the review UI:

- ``media``: the audio files (or downloads still to be done) the sources will point at;
- ``artists``: the incoming artists, each mapped to a library artist, MusicBrainz or new;
- ``releases``: incoming release groups with their tracks; a track plays a whole medium
  or a time range of one (single-file albums, chapters, radio episodes: ``slice``
  sources);
- ``loose_tracks``: incoming recordings without a release group (a single video, a radio
  tracklist entry);
- ``issues``: everything that needs the user, each with the choices available.

Each incoming item has a stable ``key`` (built from what was imported, e.g. the file
path), so that an issue keeps its id when the job runs again, and an answer given in
review still applies.

Where an item goes (``Target``) and where a cover comes from (``IncomingCover``) are
unions discriminated by ``kind`` (``oneOf`` with a ``discriminator`` in OpenAPI): each
variant carries exactly the fields it needs. Importer-specific parts that are not
catalogue rows (stage 7: the radio episode and its tracklist) are added as further
fields of ``Proposal``, referring to the items here by key, and written by the
importer's ``Importer.apply``.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ep.models import ArtistType, ReleaseGroupType, SourceKind


class Model(BaseModel):
    # Fields with defaults are still always present: required in the OpenAPI schema.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


# Where an incoming item goes: a union discriminated by ``kind``


class TargetKind(StrEnum):
    existing = "existing"
    musicbrainz = "musicbrainz"
    new = "new"
    skip = "skip"
    undecided = "undecided"


class ExistingTarget(Model):
    """An entity already in the library."""

    kind: Literal[TargetKind.existing] = TargetKind.existing
    id: uuid.UUID
    # Display text, e.g. "Pastel Blues — Nina Simone".
    label: str


class MusicBrainzTarget(Model):
    """From MusicBrainz: the library row with this MBID if there is one by then, else a
    new row with MusicBrainz's metadata."""

    kind: Literal[TargetKind.musicbrainz] = TargetKind.musicbrainz
    mbid: uuid.UUID
    label: str | None = None


class NewTarget(Model):
    """A new entity, as entered (tags, page metadata)."""

    kind: Literal[TargetKind.new] = TargetKind.new


class SkipTarget(Model):
    """Not imported: the user chose to skip it, or nothing imported refers to it."""

    kind: Literal[TargetKind.skip] = TargetKind.skip


class UndecidedTarget(Model):
    """Waiting for the answer to an open issue (or not matched yet)."""

    kind: Literal[TargetKind.undecided] = TargetKind.undecided


type Target = Annotated[
    ExistingTarget | MusicBrainzTarget | NewTarget | SkipTarget | UndecidedTarget,
    Field(discriminator="kind"),
]

UNDECIDED = UndecidedTarget()
SKIP = SkipTarget()
NEW = NewTarget()


# Incoming items


class IncomingArtist(Model):
    key: str
    name: str
    sort_name: str | None = None
    disambiguation: str = ""
    type: ArtistType | None = None
    mbid: uuid.UUID | None = None
    target: Target = UNDECIDED


class IncomingCreditPart(Model):
    artist_key: str
    credited_name: str
    join_phrase: str = ""


class Origin(Model):
    """A remote source a medium comes from (a YouTube link, a Bandcamp page): becomes the
    parent source of the file."""

    kind: SourceKind
    uri: str


class IncomingMedium(Model):
    """An audio file: in place (``path``), or to be downloaded by EP (``download``).

    A download is done by the framework after review (``Importer.download``), into a
    work directory of the job; on apply the file is moved to its place in the managed
    library and ``path`` is updated to it.
    """

    key: str
    # Absolute path as the server sees it; null while a download is pending.
    path: str | None
    # True if EP downloaded it into the managed library (``source.managed``).
    managed: bool = False
    # Where it comes from: becomes the parent source of the file. Required for downloads.
    origin: Origin | None = None
    # A download still to be done.
    download: bool = False
    codec: str | None = None
    duration_ms: int | None = None
    size: int | None = None
    sha256: str | None = None

    @model_validator(mode="after")
    def _download_has_origin(self) -> Self:
        if self.download and self.origin is None:
            raise ValueError("a medium to download needs an origin")
        return self


# Covers: a union discriminated by ``kind``


class CoverKind(StrEnum):
    embedded = "embedded"
    file = "file"
    url = "url"


class EmbeddedCover(Model):
    """The picture embedded in a medium's tags."""

    kind: Literal[CoverKind.embedded] = CoverKind.embedded
    medium_key: str


class FileCover(Model):
    """An image file, e.g. cover.jpg next to the audio files."""

    kind: Literal[CoverKind.file] = CoverKind.file
    path: str


class UrlCover(Model):
    """An image on the web (Bandcamp artwork, a video thumbnail)."""

    kind: Literal[CoverKind.url] = CoverKind.url
    url: str


type IncomingCover = Annotated[EmbeddedCover | FileCover | UrlCover, Field(discriminator="kind")]


class IncomingTrack(Model):
    key: str
    # The medium that plays it; null for a recording without audio (e.g. a radio
    # tracklist entry not found in the episode).
    medium_key: str | None
    # A time range within the medium (a ``slice`` source); null: the whole medium.
    start_ms: int | None = None
    end_ms: int | None = None
    disc: int = 1
    # Position on the disc; for a loose track, its place in the input (e.g. the
    # tracklist position of a radio episode entry).
    position: int
    title: str
    credit: list[IncomingCreditPart]
    length_ms: int | None = None
    isrcs: list[str] = Field(default_factory=list)
    # MusicBrainz recording and release track.
    mbid: uuid.UUID | None = None
    mb_track_id: uuid.UUID | None = None
    # The recording this track maps to.
    target: Target = UNDECIDED


class IncomingRelease(Model):
    key: str
    title: str
    credit: list[IncomingCreditPart]
    type: ReleaseGroupType = ReleaseGroupType.album
    date: str | None = None
    barcode: str | None = None
    # MusicBrainz release group and the release the tracks come from.
    mbid: uuid.UUID | None = None
    release_mbid: uuid.UUID | None = None
    cover: IncomingCover | None = None
    tracks: list[IncomingTrack]
    # The release group this maps to.
    target: Target = UNDECIDED


# Issues


class IssueKind(StrEnum):
    # Several library artists could be this incoming artist.
    uncertain_artist = "uncertain_artist"
    # Several library release groups could be this incoming release.
    uncertain_release_group = "uncertain_release_group"
    # Several library recordings could be this incoming track.
    uncertain_recording = "uncertain_recording"
    # Several MusicBrainz releases could be this incoming release.
    uncertain_metadata = "uncertain_metadata"
    # The release group is in the library from another MusicBrainz release.
    release_conflict = "release_conflict"
    # The metadata the item refers to does not exist (a tagged MBID unknown to
    # MusicBrainz, a page that is gone).
    metadata_not_found = "metadata_not_found"
    # A file could not be read as audio.
    unreadable_file = "unreadable_file"
    # A download failed.
    download_failed = "download_failed"


class Choice(StrEnum):
    # Use one of the issue's candidates (``Answer.candidate_id``).
    pick_candidate = "pick_candidate"
    # Create a new entity instead of using a candidate.
    create_new = "create_new"
    # Import the item as entered (tags, page metadata), without MusicBrainz.
    keep_as_entered = "keep_as_entered"
    # Do not import this item; the rest of the job goes ahead.
    skip = "skip"
    # Try again (read the file, download, look up); not stored as a decision.
    retry = "retry"


class SubjectType(StrEnum):
    artist = "artist"
    release = "release"
    track = "track"
    medium = "medium"


class Subject(Model):
    """The incoming item an issue is about (``key`` in ``artists``, ``releases``, the
    releases' ``tracks``, ``loose_tracks`` or ``media``)."""

    type: SubjectType
    key: str
    label: str


class CandidateType(StrEnum):
    artist = "artist"
    release_group = "release_group"
    recording = "recording"
    musicbrainz_release = "musicbrainz_release"


class IssueCandidate(Model):
    type: CandidateType
    # Library ID, or the MBID for MusicBrainz candidates.
    id: uuid.UUID
    title: str
    # Artist credit, disambiguation.
    subtitle: str | None = None
    # Further facts: date, country, format, track count, length.
    detail: str | None = None
    # 0-100, higher is better.
    score: float


class Answer(Model):
    choice: Choice
    # For ``pick_candidate``: one of the issue's candidates.
    candidate_id: uuid.UUID | None = None


class Issue(Model):
    # Stable across runs of the job: "<kind>:<subject key>".
    id: str
    kind: IssueKind
    message: str
    subject: Subject
    # Best first; empty for kinds without candidates.
    candidates: list[IssueCandidate] = Field(default_factory=list)
    choices: list[Choice]
    # The user's answer; null while the issue is open.
    answer: Answer | None = None


def issue_id(kind: IssueKind, subject_key: str) -> str:
    return f"{kind}:{subject_key}"


class Proposal(Model):
    media: list[IncomingMedium] = Field(default_factory=list)
    artists: list[IncomingArtist] = Field(default_factory=list)
    releases: list[IncomingRelease] = Field(default_factory=list)
    loose_tracks: list[IncomingTrack] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    def open_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.answer is None]

    def medium(self, key: str) -> IncomingMedium:
        return next(medium for medium in self.media if medium.key == key)

    def artist(self, key: str) -> IncomingArtist:
        return next(artist for artist in self.artists if artist.key == key)

    def all_tracks(self) -> list[IncomingTrack]:
        return [track for release in self.releases for track in release.tracks] + list(
            self.loose_tracks
        )


# Log


class LogLevel(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"


class LogEntry(Model):
    at: datetime
    level: LogLevel
    message: str
