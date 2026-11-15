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
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ep.models import ReleaseGroupType, SourceKind


class Model(BaseModel):
    # Fields with defaults are still always present: required in the OpenAPI schema.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


# Where an incoming item goes


class TargetKind(StrEnum):
    # An entity already in the library (``Target.id``).
    existing = "existing"
    # From MusicBrainz (``Target.mbid``): the library row with that MBID, or a new one.
    musicbrainz = "musicbrainz"
    # A new entity, as entered (tags, page metadata).
    new = "new"
    # Not imported (the user chose to skip it, or it is already in the library).
    skip = "skip"
    # Decided by an open issue.
    undecided = "undecided"


class Target(Model):
    kind: TargetKind
    id: uuid.UUID | None = None
    mbid: uuid.UUID | None = None
    # Display text of the existing or MusicBrainz entity, e.g. "Pastel Blues — Nina Simone".
    label: str | None = None


UNDECIDED = Target(kind=TargetKind.undecided)
SKIP = Target(kind=TargetKind.skip)
NEW = Target(kind=TargetKind.new)


# Incoming items


class IncomingArtist(Model):
    key: str
    name: str
    sort_name: str | None = None
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
    """An audio file: in place (``path``), or to be downloaded (``download``) on apply."""

    key: str
    # Absolute path as the server sees it; null until a pending download is done.
    path: str | None
    # True if EP downloads it into the managed library (``source.managed``).
    managed: bool = False
    origin: Origin | None = None
    # A download still to be done before applying (``origin`` says from where).
    download: bool = False
    codec: str | None = None
    duration_ms: int | None = None
    size: int | None = None
    sha256: str | None = None
    # Set when the file is already a source: nothing is imported for it.
    existing_source_id: uuid.UUID | None = None


class CoverKind(StrEnum):
    # The picture embedded in a medium's tags (``medium_key``).
    embedded = "embedded"
    # An image file (``path``), e.g. cover.jpg next to the audio files.
    file = "file"
    # An image URL (``url``).
    url = "url"


class IncomingCover(Model):
    kind: CoverKind
    medium_key: str | None = None
    path: str | None = None
    url: str | None = None


class IncomingTrack(Model):
    key: str
    # The medium that plays it; null for a recording without audio (e.g. a radio
    # tracklist entry not found in the episode).
    medium_key: str | None
    # A time range within the medium (a ``slice`` source); null: the whole medium.
    start_ms: int | None = None
    end_ms: int | None = None
    disc: int = 1
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
