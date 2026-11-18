"""Request and response schemas.

Create schemas list required fields without defaults. Update (PATCH) schemas default
every field to ``MISSING``: omitted fields are left alone, an explicit ``null`` clears a
nullable field (as in JSON Merge Patch), and non-nullable fields do not accept ``null``.

Timestamps in responses are ``Timestamp``: RFC 3339 in UTC with three fractional digits.
"""

import uuid
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

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
    model_validator,
)

from ep import covers
from ep.importers.proposal import Choice, IssueKind, LogEntry, Proposal
from ep.links import LinkKind
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
from ep.models.base import Timestamp
from ep.notes.links import format_link
from ep.playback import PlayState
from ep.service_links import LinkService

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
    created_at: Timestamp
    updated_at: Timestamp


def _link(type: EntityType, id: uuid.UUID, mbid: uuid.UUID | None, title: str) -> str:
    return format_link(type, mbid or id, title)


# Artist credits


class NewArtistIn(BaseModel):
    """An artist created with the credit (e.g. while writing a note)."""

    name: NonEmptyStr
    # Defaults to the name.
    sort_name: NonEmptyStr | None = None
    disambiguation: str = ""
    type: ArtistType | None = None


class ArtistCreditNameIn(BaseModel):
    """One artist of a credit: an existing artist (``artist_id``) or a new one
    (``new_artist``), exactly one of the two."""

    artist_id: uuid.UUID | None = None
    new_artist: NewArtistIn | None = None
    # Defaults to the artist's name.
    credited_name: NonEmptyStr | None = None
    # The text after this artist, e.g. " feat. " or " & ".
    join_phrase: str = ""

    @model_validator(mode="after")
    def _one_artist(self) -> Self:
        if (self.artist_id is None) == (self.new_artist is None):
            raise ValueError("give exactly one of artist_id and new_artist")
        return self


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
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

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
    # It has a note (``GET /notes/...``).
    has_note: bool
    type: ArtistType | None

    @computed_field
    @property
    def link(self) -> str:
        """The note link to this artist, with its name as display text."""
        return _link(EntityType.artist, self.id, self.mbid, self.name)


class ArtistCountsRead(BaseModel):
    # Release groups the artist is credited on.
    release_groups: int
    # Recordings credited to the artist that are on no release group (loose tracks),
    # radio-only ones left out.
    loose_recordings: int
    # Release groups not credited to the artist with a track whose recording is
    # (featured appearances, "Appears on").
    appearances: int
    # Radio episodes that played something credited to the artist.
    radio_episodes: int


class ArtistDetail(ArtistRead):
    counts: ArtistCountsRead
    # Known only from radio tracklists (``ep.radio``): hidden from lists by default.
    radio_only: bool


class ArtistPositionRead(BaseModel):
    # Index of the artist's row in ``GET /artists`` (the list asked about).
    offset: int
    # The artist at ``offset``; null if ``offset`` is past the end of the list.
    id: uuid.UUID | None


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
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

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
    # It has a note (``GET /notes/...``).
    has_note: bool
    cover_path: str | None = Field(exclude=True)

    @computed_field
    @property
    def cover_url(self) -> str | None:
        """Path of the cover image (``GET /covers/release-group/{id}``, with a version
        that changes with the image), if there is one."""
        return covers.image_url("release-group", self.id, self.cover_path)

    @computed_field
    @property
    def link(self) -> str:
        """The note link to this release group, with its title as display text."""
        return _link(EntityType.release_group, self.id, self.mbid, self.title)


class PlayStateCountsRead(BaseModel):
    """How many tracks are in each ``PlayState``."""

    file: int
    slice: int
    radio_excerpt: int
    file_missing: int
    link_only: int
    no_source: int


class ReleaseGroupDetail(ReleaseGroupRead):
    track_count: int
    disc_count: int
    # Sum of the tracks' recording lengths (those known).
    length_ms: int
    play_states: PlayStateCountsRead
    # The kinds of link the link-only tracks have (e.g. spotify), each once.
    link_kinds: list[SourceKind]


# Recordings


class RecordingCreate(BaseModel):
    mbid: uuid.UUID | None = None
    title: NonEmptyStr
    artist_credit: ArtistCreditIn
    length_ms: NonNegativeInt | None = None


class RecordingUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

    mbid: uuid.UUID | MISSING | None = MISSING
    title: NonEmptyStr | MISSING = MISSING
    artist_credit: ArtistCreditIn | MISSING = MISSING
    length_ms: NonNegativeInt | MISSING | None = MISSING


class PreferredSourceWrite(BaseModel):
    """The source to play instead of the default choice: one of the recording's
    ``playable`` sources; null (or omitted): the default."""

    source_id: uuid.UUID | None = None


class RecordingRead(Read):
    mbid: uuid.UUID | None
    title: str
    artist_credit: ArtistCreditRead
    length_ms: int | None
    # The source chosen to play (``PUT /recordings/{id}/preferred-source``); null: the
    # default choice.
    preferred_source_id: uuid.UUID | None
    # It has a note (``GET /notes/...``) (a track's note is its recording's).
    has_note: bool

    @computed_field
    @property
    def link(self) -> str:
        """The note link to this recording, with its title as display text."""
        return _link(EntityType.recording, self.id, self.mbid, self.title)


class RecordingTrackRead(Read):
    """A track of a recording, seen from the recording: with its release group."""

    disc: int
    position: int
    title: str | None
    release_group: ReleaseGroupRead


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
    # Length of the whole file, if known (the scale of a scrubber over the file).
    file_duration_ms: int | None
    # The file's container as a media type, the ``Content-Type`` its stream is served
    # with (audio/mpeg, audio/mp4, audio/flac, audio/ogg, audio/webm, ...), and its
    # audio codec as ffprobe names it (mp3, aac, alac, flac, opus, vorbis, ...; null if
    # not known). Enough to choose a decoder before fetching any of the file.
    media_type: str
    codec: str | None


class LinkRead(BaseModel):
    """A link source: somewhere else the recording can be found."""

    source_id: uuid.UUID
    kind: SourceKind
    url: str


class RadioEntryRefRead(BaseModel):
    """A radio tracklist entry (``GET /radio/episodes/{episode_id}`` has it)."""

    track_id: uuid.UUID
    episode_id: uuid.UUID


class PlayabilityRead(BaseModel):
    """How a recording plays here (``ep.playback.playabilities``)."""

    state: PlayState
    # What plays: for ``file``, ``slice`` and ``radio_excerpt``; for ``file_missing`` a
    # radio excerpt if there is one; null otherwise.
    playback: PlaybackRead | None
    # The radio entry ``playback`` plays, if it is a radio excerpt.
    radio_entry: RadioEntryRefRead | None
    # The recording's oldest link (in any state; for ``link_only`` the one to open).
    link: LinkRead | None
    # ``file_missing``: the file source not found on disk (give it a new ``uri`` with
    # ``PATCH /recordings/{id}/sources/{source_id}`` or ``PATCH /sources/{id}`` to
    # locate it).
    missing_file_source_id: uuid.UUID | None


class PlayableRecordingRead(RecordingRead):
    playability: PlayabilityRead


class RecordingDetail(PlayableRecordingRead):
    tracks: list[RecordingTrackRead]


# Tracks


class NewRecordingIn(BaseModel):
    """A recording created with the track."""

    title: NonEmptyStr
    # Defaults to the release group's credit.
    artist_credit: ArtistCreditIn | None = None
    length_ms: NonNegativeInt | None = None


class TrackCreate(BaseModel):
    """A track: of an existing recording (``recording_id``) or of a new one
    (``new_recording``), exactly one of the two."""

    release_group_id: uuid.UUID
    recording_id: uuid.UUID | None = None
    new_recording: NewRecordingIn | None = None
    disc: PositiveInt = 1
    # Defaults to after the last track of the disc.
    position: NonNegativeInt | None = None
    # Overrides the recording title on this release group.
    title: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _one_recording(self) -> Self:
        if (self.recording_id is None) == (self.new_recording is None):
            raise ValueError("give exactly one of recording_id and new_recording")
        return self


class TrackPlace(BaseModel):
    id: uuid.UUID
    disc: PositiveInt
    position: NonNegativeInt


class TrackOrderWrite(BaseModel):
    """Every track of the release group with its new disc and position."""

    tracks: list[TrackPlace]


class TrackUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

    recording_id: uuid.UUID | MISSING = MISSING
    disc: PositiveInt | MISSING = MISSING
    position: NonNegativeInt | MISSING = MISSING
    title: NonEmptyStr | MISSING | None = MISSING


class TrackRead(Read):
    release_group_id: uuid.UUID
    disc: int
    position: int
    # The title on this release group, if it differs from the recording's.
    title: str | None
    recording: RecordingRead
    playability: PlayabilityRead


# Radio


class RadioShowUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

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
        """Path of the show's image (``GET /covers/radio-show/{id}``, versioned), if any."""
        return covers.image_url("radio-show", self.id, self.image_path)

    @computed_field
    @property
    def link(self) -> str:
        """The note link to this show, with its name as display text."""
        return _link(EntityType.radio_show, self.id, None, self.name)


class RadioShowDetail(RadioShowRead):
    episode_count: int


class RadioEpisodeUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

    name: NonEmptyStr | MISSING = MISSING
    description: str | MISSING = MISSING
    broadcast_at: AwareDatetime | MISSING | None = MISSING
    location: NonEmptyStr | MISSING | None = MISSING


class RadioEpisodeRead(Read):
    show_id: uuid.UUID
    slug: str
    name: str
    description: str
    broadcast_at: Timestamp | None
    location: str | None
    # The episode page.
    url: str
    # The episode's audio file (a ``file`` source); null if it was not downloaded.
    source_id: uuid.UUID | None
    image_path: str | None = Field(exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        """Path of the episode's image (``GET /covers/radio-episode/{id}``, versioned), if
        any."""
        return covers.image_url("radio-episode", self.id, self.image_path)

    @computed_field
    @property
    def link(self) -> str:
        """The note link to this episode, with its name as display text."""
        return _link(EntityType.radio_episode, self.id, None, self.name)


class RadioEntryState(StrEnum):
    # Matched to a recording the library has other than from radio.
    in_library = "in_library"
    # Matched to a recording known only from radio (this excerpt plays it).
    excerpt_only = "excerpt_only"
    not_matched = "not_matched"


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
    state: RadioEntryState


class RadioEpisodeDetail(RadioEpisodeRead):
    show: RadioShowRead
    # How to play the whole episode.
    playback: PlaybackRead | None
    # Length of the episode file, if known (the scrubber's scale; the last entry may
    # have no end).
    duration_ms: int | None
    tracks: list[RadioEpisodeTrackRead]


class RadioEpisodeTrackUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

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
    # How to play the entry within the episode; null if it has no time or the episode
    # no file.
    playback: PlaybackRead | None


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


class RecordingSourceCreate(BaseModel):
    """A source of the recording named in the path."""

    kind: SourceKind
    # Absolute path for file (must exist; a host path under ``EP_HOST_PATHS`` is mapped
    # to the server's), URL/URI for remote kinds, omitted for slice.
    uri: NonEmptyStr | None = None
    parent_id: uuid.UUID | None = None
    # Slice only: range within the parent. No end: to the end of the parent.
    start_ms: NonNegativeInt | None = None
    end_ms: PositiveInt | None = None
    managed: bool = False
    # For file sources, codec/duration_ms/size are read from the file when omitted (and
    # always its media type).
    codec: NonEmptyStr | None = None
    duration_ms: NonNegativeInt | None = None
    size: NonNegativeInt | None = None
    sha256: Sha256 | None = None


class SourceCreate(RecordingSourceCreate):
    """A source of any recording, or of none (a container: an album or episode file)."""

    recording_id: uuid.UUID | None = None


class SourceUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

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


class SourceRead(Read):
    kind: SourceKind
    uri: str | None
    parent_id: uuid.UUID | None
    start_ms: int | None
    end_ms: int | None
    recording_id: uuid.UUID | None
    managed: bool
    # A file's container as a media type and its audio codec (``PlaybackRead``).
    media_type: str | None
    codec: str | None
    duration_ms: int | None
    size: int | None
    sha256: str | None
    # Stream facts of a file: sample rate (Hz), bits per sample (lossless formats),
    # bit rate (bit/s), channels.
    sample_rate: int | None
    bit_depth: int | None
    bit_rate: int | None
    channels: int | None
    # A file's path on the host running the server (``EP_HOST_PATHS``), for showing it
    # in a file manager; null for other kinds, or a path outside the mapped directories.
    host_path: str | None = None
    # Null if the source does not resolve to a file (e.g. a remote-only link).
    playback: PlaybackRead | None = None


class SourceRole(StrEnum):
    """What a source of a recording is, as a way to play it."""

    # A file of the recording's own.
    file = "file"
    # A time range of a file shared with other recordings (single-file album).
    slice = "slice"
    # A radio tracklist entry's range of the episode file.
    radio_excerpt = "radio_excerpt"
    # A link to elsewhere (not playable here).
    link = "link"


class FileRead(BaseModel):
    """The file a source plays from."""

    source_id: uuid.UUID
    path: str
    host_path: str | None
    exists: bool
    media_type: str
    codec: str | None
    sample_rate: int | None
    bit_depth: int | None
    bit_rate: int | None
    channels: int | None
    duration_ms: int | None
    # In bytes.
    size: int | None


class SourceImportRead(Read):
    """The import job that added a source's file (``GET /imports/{id}`` has the rest)."""

    kind: ImportKind
    # A directory path for ``folder``; the page URL for the other kinds.
    input: str
    # ``folder``: the input's path on the host (``EP_HOST_PATHS``).
    input_host_path: str | None
    # The watched directory whose watcher queued it; null if queued by hand.
    watch_dir_id: uuid.UUID | None
    state: JobState


class RadioSliceRead(BaseModel):
    """The radio entry a slice plays."""

    track_id: uuid.UUID
    episode: RadioEpisodeRead
    show: RadioShowRead
    offset_is_estimate: bool


class RecordingSourceRead(BaseModel):
    """A source of a recording, as the sources list shows it."""

    source: SourceRead
    role: SourceRole
    # Where it came from: its parent sources, nearest first (a file's download link).
    ancestors: list[SourceRead]
    # The file it plays from; null for links (and broken chains).
    file: FileRead | None
    # The import that added the file it plays from (or, for a source without a file,
    # the source itself); null for sources added by hand or by older imports.
    imported_by: SourceImportRead | None
    radio: RadioSliceRead | None
    # It resolves to a file, so it can be chosen to play (links cannot).
    playable: bool
    # The user chose it (``PUT /recordings/{id}/preferred-source``).
    preferred: bool
    # It is what plays when the recording is played: the preferred source if its file
    # is there, else the server's default choice (``PlayabilityRead.playback``). At most
    # one source of the list.
    will_play: bool


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
    # station of a show, the show of an episode.
    subtitle: str | None
    # A release group's cover; for a recording, the cover of a release group it is on;
    # a show's or episode's image.
    cover_url: str | None
    # Facts for a second line: a release group's type and date; a recording's length
    # and the (oldest) release group it is on; an episode's broadcast date (YYYY-MM-DD).
    release_group_type: ReleaseGroupType | None
    date: str | None
    length_ms: int | None
    release_group_title: str | None
    # For writing a link to it: the UUID to use (the MBID if there is one, else the
    # id), and the whole link with the title as display text.
    link_ref: uuid.UUID
    link: str


class RadioMatchRead(BaseModel):
    """Where a radio episode plays what the search matched."""

    track_id: uuid.UUID
    recording: EntityRead
    start_ms: int | None
    offset_is_estimate: bool


class SearchHitRead(EntityRead):
    """A search hit."""

    # Higher is better; only comparable within one response. Null without a query
    # (everything with a tag).
    score: float | None
    # Release groups: their track count. Artists: their release groups and loose
    # recordings (``ArtistCountsRead``). Null for other types.
    track_count: int | None
    release_group_count: int | None
    loose_recording_count: int | None
    # Episodes found because they played a matching recording (or a track of a
    # matching release group): which entry, and when.
    radio_match: RadioMatchRead | None


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
    updated_at: Timestamp | None
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
    note_updated_at: Timestamp
    # The line around each link to the entity, in the order of the note.
    snippets: list[SnippetRead]


class NoteSearchHitRead(BaseModel):
    """A note matching a full-text search."""

    # The entity whose note it is.
    source: EntityRead
    note_updated_at: Timestamp
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
    # played anything credited to them; for a release group, the episodes that played
    # any of its tracks; empty for radio shows and episodes.
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
    """What to add from MusicBrainz (metadata only): an artist, a release (as a release
    group with its tracks) or a recording."""

    type: MusicBrainzSearchType
    mbid: uuid.UUID


# Imports (the proposal and issue structure is ``ep.importers.proposal``)


class ImportCreate(BaseModel):
    kind: ImportKind
    # ``folder``: an absolute directory path under the media roots (or the host path of
    # one, ``EP_HOST_PATHS``); ``bandcamp``: an album or track page URL; ``youtube``: a
    # video or playlist URL; ``nts``: an episode page URL.
    input: NonEmptyStr


class LinkInfoRead(BaseModel):
    """What a URL is: the kind of link, the source kind it would be stored as, and the
    import that takes it (null: not importable). All null for an unknown URL."""

    link_kind: LinkKind | None
    source_kind: SourceKind | None
    import_kind: ImportKind | None
    # The canonical form (tracking parameters dropped), as imports and sources store it.
    url: str | None


class JobAction(StrEnum):
    """What the user can do with a job now."""

    # Answer its open issues: ``POST /imports/{id}/review``.
    review = "review"
    # Run it again from the start: ``POST /imports/{id}/retry``.
    retry = "retry"
    # Remove it from the inbox: ``POST /imports/{id}/dismiss``.
    dismiss = "dismiss"
    # Stop it (queued or running) and remove it: ``POST /imports/{id}/cancel``.
    cancel = "cancel"


class ImportStep(StrEnum):
    """The user-facing steps of an import, in order; each covers pipeline stages."""

    # Fetching and resolving metadata (stages fetch, resolve).
    metadata = "metadata"
    # Matching against the library (match, propose).
    matching = "matching"
    # Downloading audio (download); skipped by imports that do not download.
    downloading = "downloading"
    # Writing to the library (apply).
    applying = "applying"


class ProgressRead(BaseModel):
    # The stage the job is in (or stopped in); null before it starts.
    stage: JobStage | None
    # The step that stage belongs to.
    step: ImportStep | None
    # Items of this stage done, out of ``total`` (null if not known yet).
    done: int
    total: int | None
    # How far the stage is, 0-1, counting the item in progress (a download) partly;
    # null if ``total`` is not known.
    fraction: float | None


class ImportCountsRead(BaseModel):
    # Incoming tracks (radio tracklist entries included) that are not skipped.
    tracks: int
    # Open issues that are failures (download failed, unreadable file, fetch failed):
    # what a retry would try again.
    failed: int


class CreatedCountsRead(BaseModel):
    """Rows the last apply created."""

    artists: int
    release_groups: int
    recordings: int
    tracks: int
    sources: int


class IssueBriefRead(BaseModel):
    """An issue of a job, in brief (``Issue`` in the proposal has everything)."""

    id: str
    kind: IssueKind
    message: str


class ImportJobSummary(Read):
    kind: ImportKind
    input: str
    # ``folder``: the input's path on the host (``EP_HOST_PATHS``).
    input_host_path: str | None
    # URL inputs: what the link is.
    link_kind: LinkKind | None
    # The watched directory whose watcher queued it; null if queued by hand.
    watch_dir_id: uuid.UUID | None
    state: JobState
    progress: ProgressRead
    error: str | None
    # What is imported (the release, the loose track, or the radio episode) and its
    # artist credit (for an episode: the show), once the proposal is built; null before.
    title: str | None
    subtitle: str | None
    # The incoming cover or image: a URL elsewhere (before it is fetched) or a path on
    # this server (``/covers/...``, once imported); null if none or not known yet.
    cover_url: str | None
    # The first open issue (a blocking one if any), for a one-line summary; the
    # proposal (``GET /imports/{id}``) has them all.
    first_issue: IssueBriefRead | None
    # Issues waiting for an answer.
    open_issues: int
    counts: ImportCountsRead
    created: CreatedCountsRead
    # A release conflict: the release group in the library that conflicts.
    existing: EntityRead | None
    # A queued job: the job it waits for (the running one, or the one queued before
    # it); null if it is next.
    waiting_for: uuid.UUID | None
    actions: list[JobAction]


class ImportsSummaryRead(BaseModel):
    """The import queue at a glance (cheap to poll)."""

    queued: int
    running: int
    needs_review: int
    failed: int
    # Progress of the running job (``ProgressRead.fraction``); null if none runs.
    running_fraction: float | None
    # Enabled watched directories.
    watching: int
    # The latest change to any job: poll this, and reload the job list when it moves.
    changed_at: Timestamp | None


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
    # Required for ``pick_candidate``: the ``id`` of one of the issue's candidates or,
    # for an uncertain artist, release group or recording, of any library row of that
    # type (found by searching the library).
    candidate_id: uuid.UUID | None = None


class ReviewRequest(BaseModel):
    answers: Annotated[list[IssueAnswer], Field(min_length=1)]


# Watched directories


class WatchDirCreate(BaseModel):
    # Absolute directory path under the media roots, e.g. /watch/Library, or its host
    # path (``EP_HOST_PATHS``).
    path: NonEmptyStr
    enabled: bool = True


class WatchDirUpdate(BaseModel):
    """Changes (PATCH): omitted fields stay as they are; ``null`` clears a nullable field."""

    enabled: bool | MISSING = MISSING


class WatchDirStatus(StrEnum):
    # Not watched (disabled).
    paused = "paused"
    # An import of a folder in it is queued or running.
    importing = "importing"
    idle = "idle"


class WatchDirRead(Read):
    path: str
    # The path on the host running the server (``EP_HOST_PATHS``).
    host_path: str | None
    enabled: bool
    # Folders that last changed before this are imported only by an explicit scan.
    baseline_at: Timestamp
    status: WatchDirStatus
    # When its watcher last queued an import.
    last_import_at: Timestamp | None


class WatchDirScan(BaseModel):
    # A directory inside the watch dir (or its host path) to limit the scan to;
    # omitted: all of it.
    path: NonEmptyStr | None = None


# The server


class ServerPathRead(BaseModel):
    # As the server sees it.
    path: str
    # On the host running the server (``EP_HOST_PATHS``); null if not mapped.
    host_path: str | None


class LibraryCountsRead(BaseModel):
    # Artists shown by default, and those known only from radio (hidden by default).
    artists: int
    radio_only_artists: int
    release_groups: int
    recordings: int
    tracks: int
    radio_shows: int
    radio_episodes: int


class ServerRead(BaseModel):
    version: str
    counts: LibraryCountsRead
    # The managed library (``EP_LIBRARY_DIR``).
    library_dir: ServerPathRead
    # The directories file sources and imports may point into (``EP_MEDIA_ROOTS``).
    media_roots: list[ServerPathRead]


# Links to other services


class ServiceLinkRead(BaseModel):
    """A link of an artist or release group to another service (``LinkService``)."""

    service: LinkService
    url: str
    # Wikipedia: the article's language code (``en``, ``de``, ...); null for the others.
    label: str | None
