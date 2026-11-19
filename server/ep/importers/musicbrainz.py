"""Catalogue rows from a MusicBrainz release (metadata only: no files, no cover).

``import_release`` adds the release's artists, artist credits, its release group (with
``release_mbid`` set to this release), recordings, tracks and recording ISRCs, reusing
rows that already have the MBID. Existing rows are not updated: edits made in EP win.

One release per release group: if the release group exists with another release, this
raises ``ReleaseConflict``. Importing the same release again adds what is missing.

Existing recordings without an MBID (a loose YouTube track, a radio tracklist entry) can
be attached instead of creating duplicates: ``propose_recordings`` runs the matcher over
the release's tracks, and ``import_release(..., recordings={track MBID: recording})``
uses the given recordings. An attached recording gains the MBID (and a length if it had
none), ISRCs and a ``track`` row, and keeps everything else: its title, credit, sources,
external IDs and notes. Recordings already at a track position of the release group
(e.g. imported earlier from tags) gain the MBID of the release's track there.

Artists and the release group work the same way: ``artists`` and ``release_group`` give
the library rows to use (the import framework passes what matching and the user decided),
and they gain their MBID. Without them, a row with the MBID is used, else a confident
match (``ep.matching``, ``auto_match``) among the rows without an MBID, else a new row.

A new artist whose MusicBrainz name is not in Latin script ("跡部進一") is named by its
Latin-script alias ("Shinichi Atobe", see ``artist_names``) and keeps the MusicBrainz
name as ``original_name``; with a ``musicbrainz`` client given, that asks MusicBrainz
for the artist's aliases (one request per such artist; a failure keeps the MusicBrainz
name). A library artist with the MBID that still has its MusicBrainz name in another
script (imported before artists were named so) is renamed alike, like a missing cover
filled in (``fill_latin_name``); one named otherwise in EP is left alone.
"""

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError
from sqlmodel import Session, col, select

from ep.credits import CreditPart, get_or_create_artist_credit
from ep.matching import Decision, MatchResult, match_artist, match_recording, match_release_group
from ep.matching.match import AUTO_MATCH_SCORE, text_score
from ep.matching.normalize import normalize_title
from ep.metadata.musicbrainz import (
    MBArtist,
    MBArtistCreditPart,
    MBRecording,
    MBRelease,
    MBReleaseGroup,
    MBTrack,
    MusicBrainz,
    MusicBrainzError,
    is_latin,
    latin_alias,
    normalize_isrc,
    render_artist_credit,
)
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistType,
    EntityType,
    ExternalId,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Track,
)
from ep.notes.tags import add_imported_tags

logger = logging.getLogger(__name__)

_ARTIST_TYPES = {
    "Person": ArtistType.person,
    "Group": ArtistType.group,
    "Orchestra": ArtistType.group,
    "Choir": ArtistType.group,
}

_PRIMARY_TYPES = {
    "Album": ReleaseGroupType.album,
    "EP": ReleaseGroupType.ep,
    "Single": ReleaseGroupType.single,
}


class ReleaseConflict(Exception):
    """The release group already exists in EP with a different release."""


class RecordingConflict(Exception):
    """A pre-matched recording cannot take the MBID of the release track it is matched to."""


def propose_recordings(
    session: Session, release: MBRelease
) -> dict[uuid.UUID, MatchResult[Recording]]:
    """Match every track of a release against the library, keyed by track MBID.

    A track whose recording is already in the library matches it by MBID. Recordings with
    another MBID are never candidates. A local recording auto-matched by two tracks of
    different MusicBrainz recordings is demoted to ``needs_review`` on both. Pass the
    accepted matches to ``import_release`` as ``recordings``, e.g.
    ``{track_id: result.match for track_id, result in proposals.items() if result.match}``.
    """
    results: dict[uuid.UUID, MatchResult[Recording]] = {}
    for track in _tracks(release):
        mb_recording = track.recording
        credit = track.artist_credit or mb_recording.artist_credit
        results[track.id] = match_recording(
            session,
            track.title,
            artist=render_artist_credit(credit),
            artist_names=[name for part in credit for name in (part.name, part.artist.name)],
            length_ms=track.length or mb_recording.length,
            mbid=mb_recording.id,
            external_ids=[("isrc", isrc) for isrc in mb_recording.isrcs],
        )
    claimed: dict[uuid.UUID, set[uuid.UUID]] = {}
    for track in _tracks(release):
        if match := results[track.id].match:
            claimed.setdefault(match.id, set()).add(track.recording.id)
    for track in _tracks(release):
        result = results[track.id]
        if result.match and len(claimed[result.match.id]) > 1:
            results[track.id] = MatchResult(Decision.needs_review, result.candidates)
    return results


def import_release(
    session: Session,
    release: MBRelease,
    recordings: Mapping[uuid.UUID, Recording] | None = None,
    *,
    artists: Mapping[uuid.UUID, Artist | None] | None = None,
    release_group: ReleaseGroup | None = None,
    tags: Sequence[str] = (),
    musicbrainz: MusicBrainz | None = None,
    log: Callable[[str], None] | None = None,
) -> ReleaseGroup:
    """Add a release (fetched with ``MusicBrainz.get_release``) to the catalogue.

    ``recordings`` maps track MBIDs to existing recordings to use for those tracks
    (see ``propose_recordings``); raises ``RecordingConflict`` if one has another MBID,
    or the track's recording is already in the library as a different row, or one
    recording is given for tracks of different MusicBrainz recordings.

    ``artists`` maps artist MBIDs to the library artist to use (it gains the MBID), or to
    None for "a new artist" (unless one with the MBID exists). ``release_group`` is the
    library release group to use (it gains the MBIDs; ``ReleaseConflict`` if it has
    others). Artists and a release group not given are found by MBID, else by a
    confident match, else created.

    A release group this creates gets the genres of the MusicBrainz release group and
    release, and ``tags`` (e.g. Bandcamp's), as tags; one already in the library keeps
    the tags it has (``ep.notes.tags``).

    ``musicbrainz``: asked for the aliases of a new artist not named in Latin script
    (see ``artist_names``), and of a library artist of the release (not given in
    ``artists``) still named so (``fill_latin_name``); ``log`` is told of a rename.

    Flushes but does not commit. Returns the release group.
    """
    if release.release_group is None:
        raise ValueError("release lookup without its release group (inc=release-groups)")
    recordings = recordings or {}
    _check_prematched(session, release, recordings)
    resolver = _ArtistResolver(session, artists or {}, musicbrainz, log)
    resolver.fill_latin_names(_release_artists(release))
    release_group = _release_group(session, resolver, release, release.release_group, release_group)
    created = release_group in session.new
    session.flush()
    if created:
        genres = [genre.name for genre in (*release.release_group.genres, *release.genres)]
        add_imported_tags(session, EntityType.release_group, release_group.id, [*genres, *tags])
    existing = {(track.disc, track.position): track for track in release_group.tracks}
    for disc_index, medium in enumerate(release.media, start=1):
        disc = medium.position or disc_index
        for mb_track in medium.tracks:
            prematched = recordings.get(mb_track.id)
            if (track := existing.get((disc, mb_track.position))) is not None:
                _attach_existing_track(session, track, mb_track, prematched)
                continue
            recording = _recording(
                session, resolver, mb_track.recording, mb_track.artist_credit, prematched
            )
            title = mb_track.title if mb_track.title != recording.title else None
            session.add(
                Track(
                    release_group_id=release_group.id,
                    recording_id=recording.id,
                    disc=disc,
                    position=mb_track.position,
                    title=title,
                )
            )
    session.flush()
    session.expire(release_group, ["tracks"])
    return release_group


def _attach_existing_track(
    session: Session, track: Track, mb_track: MBTrack, prematched: Recording | None
) -> None:
    """The release group has a track at this position already: its recording gains the
    MBID if it has none, no other recording has it, and the titles agree (or it was
    pre-matched to this track)."""
    recording = track.recording
    mbid = mb_track.recording.id
    if prematched not in (None, recording) or recording.mbid not in (None, mbid):
        return
    if recording.mbid is None:
        if prematched is None and not _same_title(recording.title, mb_track):
            return
        if session.exec(select(Recording.id).where(Recording.mbid == mbid)).first():
            return
        recording.mbid = mbid
        if recording.length_ms is None:
            recording.length_ms = mb_track.recording.length
    add_isrcs(session, recording.id, mb_track.recording.isrcs)


def _tracks(release: MBRelease) -> list[MBTrack]:
    return [track for medium in release.media for track in medium.tracks]


def _check_prematched(
    session: Session, release: MBRelease, recordings: Mapping[uuid.UUID, Recording]
) -> None:
    tracks = {track.id: track for track in _tracks(release)}
    if unknown := set(recordings) - set(tracks):
        raise ValueError(f"not tracks of release {release.id}: {sorted(map(str, unknown))}")
    mbids: dict[uuid.UUID, uuid.UUID] = {}
    for track_id, recording in recordings.items():
        mbid = tracks[track_id].recording.id
        if recording.mbid not in (None, mbid):
            raise RecordingConflict(
                f"recording {recording.id} ({recording.title!r}) has MBID {recording.mbid}, "
                f"track {track_id} is recording {mbid}"
            )
        if mbids.setdefault(recording.id, mbid) != mbid:
            raise RecordingConflict(
                f"recording {recording.id} ({recording.title!r}) is given for tracks of "
                f"recordings {mbids[recording.id]} and {mbid}"
            )
        existing = session.exec(select(Recording.id).where(Recording.mbid == mbid)).first()
        if existing not in (None, recording.id):
            raise RecordingConflict(
                f"recording {mbid} is already in the library as {existing}, "
                f"not {recording.id} ({recording.title!r})"
            )


def _release_group(
    session: Session,
    resolver: _ArtistResolver,
    release: MBRelease,
    mb_release_group: MBReleaseGroup,
    given: ReleaseGroup | None,
) -> ReleaseGroup:
    release_group = (
        given
        or session.exec(
            select(ReleaseGroup).where(ReleaseGroup.mbid == mb_release_group.id)
        ).first()
    )
    credit_parts = mb_release_group.artist_credit or release.artist_credit
    if release_group is None:
        result = match_release_group(
            session,
            mb_release_group.title,
            artist=render_artist_credit(credit_parts),
            artist_names=[name for part in credit_parts for name in (part.name, part.artist.name)],
            mbid=mb_release_group.id,
            release_mbid=release.id,
            barcode=release.barcode,
            date=mb_release_group.first_release_date,
            release_date=release.date,
        )
        release_group = result.match
    if release_group is not None:
        if release_group.mbid not in (
            None,
            mb_release_group.id,
        ) or release_group.release_mbid not in (
            None,
            release.id,
        ):
            raise ReleaseConflict(
                f"release group {release_group.mbid} ({release_group.title!r}) is already "
                f"imported from release {release_group.release_mbid}, not {release.id} "
                f"of {mb_release_group.id}"
            )
        release_group.mbid, release_group.release_mbid = mb_release_group.id, release.id
        if release_group.barcode is None:
            release_group.barcode = release.barcode
        return release_group
    credit = resolver.credit(credit_parts)
    release_group = ReleaseGroup(
        mbid=mb_release_group.id,
        release_mbid=release.id,
        title=mb_release_group.title,
        artist_credit_id=credit.id,
        type=release_group_type(mb_release_group),
        date=mb_release_group.first_release_date or release.date,
        barcode=release.barcode,
    )
    session.add(release_group)
    return release_group


def release_group_type(release_group: MBReleaseGroup) -> ReleaseGroupType:
    """The EP type of a MusicBrainz release group (Compilation wins over the primary type)."""
    if "Compilation" in release_group.secondary_types:
        return ReleaseGroupType.compilation
    return _PRIMARY_TYPES.get(release_group.primary_type or "", ReleaseGroupType.other)


def _recording(
    session: Session,
    resolver: _ArtistResolver,
    mb_recording: MBRecording,
    track_credit: Sequence[MBArtistCreditPart],
    prematched: Recording | None,
) -> Recording:
    """The recording for a track: pre-matched (gaining the MBID), by MBID, or new."""
    if prematched is not None:
        recording = prematched
        recording.mbid = mb_recording.id
        if recording.length_ms is None:
            recording.length_ms = mb_recording.length
    else:
        recording = session.exec(select(Recording).where(Recording.mbid == mb_recording.id)).first()
    if recording is None:
        credit = resolver.credit(mb_recording.artist_credit or track_credit)
        recording = Recording(
            mbid=mb_recording.id,
            title=mb_recording.title,
            artist_credit_id=credit.id,
            length_ms=mb_recording.length,
        )
        session.add(recording)
    add_isrcs(session, recording.id, mb_recording.isrcs)
    return recording


def _same_title(title: str, mb_track: MBTrack) -> bool:
    ours = normalize_title(title)
    return any(
        text_score(ours, normalize_title(theirs)) >= AUTO_MATCH_SCORE
        for theirs in (mb_track.title, mb_track.recording.title)
    )


def import_recording(
    session: Session,
    mb_recording: MBRecording,
    artists: Mapping[uuid.UUID, Artist | None] | None = None,
    musicbrainz: MusicBrainz | None = None,
    log: Callable[[str], None] | None = None,
) -> Recording:
    """The library recording for a MusicBrainz recording (fetched with ``get_recording``):
    the row with its MBID, or a new standalone recording (no track) with its artists
    (``artists``, ``musicbrainz`` and ``log`` as for ``import_release``) and ISRCs.
    Flushes nothing."""
    resolver = _ArtistResolver(session, artists or {}, musicbrainz, log)
    return _recording(session, resolver, mb_recording, mb_recording.artist_credit, None)


def add_isrcs(session: Session, recording_id: uuid.UUID, isrcs: Sequence[str]) -> None:
    """Add the ISRCs (normalised) the recording does not have yet."""
    values = {normalize_isrc(isrc) for isrc in isrcs}
    known = set(
        session.exec(
            select(ExternalId.value).where(
                ExternalId.entity_type == EntityType.recording,
                ExternalId.entity_id == recording_id,
                ExternalId.scheme == "isrc",
                col(ExternalId.value).in_(values),
            )
        )
    )
    for value in sorted(values - known):
        session.add(
            ExternalId(
                entity_type=EntityType.recording,
                entity_id=recording_id,
                scheme="isrc",
                value=value,
            )
        )


def artist_type(mb_artist: MBArtist) -> ArtistType | None:
    return _ARTIST_TYPES.get(mb_artist.type, ArtistType.other) if mb_artist.type else None


def _release_artists(release: MBRelease) -> list[MBArtist]:
    """Every artist credited on the release, its release group, tracks or recordings."""
    parts = [*release.artist_credit]
    if release.release_group is not None:
        parts += release.release_group.artist_credit
    for medium in release.media:
        for track in medium.tracks:
            parts += [*track.artist_credit, *track.recording.artist_credit]
    return list({part.artist.id: part.artist for part in parts}.values())


def needs_latin_name(artist: Artist, mb_name: str) -> bool:
    """Whether a library artist with an MBID still has the name MusicBrainz gave it
    (``mb_name``), not in Latin script and without original name: imported before
    artists were named by their Latin-script alias, and not renamed in EP since."""
    return (
        artist.mbid is not None
        and artist.original_name is None
        and artist.name == mb_name
        and not is_latin(artist.name)
    )


def fill_latin_name(artist: Artist, names: ArtistNames) -> bool:
    """Rename a library artist that ``needs_latin_name`` to the names ``artist_names``
    found (the original name is the old name; the sort name only if the old one is not
    in Latin script either). False if there is no Latin-script name. Credits keep their
    credited names (they are never edited in place, ``ep.credits``)."""
    if names.original_name is None or names.original_name != artist.name:
        return False
    artist.name, artist.original_name = names.name, names.original_name
    if not is_latin(artist.sort_name):
        artist.sort_name = names.sort_name
    return True


def fill_latin_names_of(
    artists: Iterable[Artist],
    musicbrainz: MusicBrainz,
    log: Callable[[str], None],
    looked_up: set[uuid.UUID],
) -> None:
    """``fill_latin_name`` for library artists known only as rows (an album imported
    before, whose release is not looked up again): one lookup with aliases for each
    with an MBID, no original name and a name not in Latin script; MusicBrainz's name
    must still be theirs. A failure keeps the names (logged). ``looked_up``: the MBIDs
    whose aliases were read already (left out), updated."""
    for artist in artists:
        if (
            artist.mbid is None
            or artist.mbid in looked_up
            or artist.original_name is not None
            or is_latin(artist.name)
        ):
            continue
        looked_up.add(artist.mbid)
        try:
            mb_artist = musicbrainz.get_artist(artist.mbid)
        except (MusicBrainzError, ValidationError) as e:
            log(f"aliases of artist {artist.name!r} not read, name kept: {e}")
            continue
        old = artist.name
        if needs_latin_name(artist, mb_artist.name) and fill_latin_name(
            artist, artist_names(mb_artist, None)
        ):
            log(f"artist {old!r} renamed to {artist.name!r}")


def credited_as(artist: Artist, credited_name: str) -> str:
    """The name to credit an artist with: its name where the credit has its original
    name (MusicBrainz credits "跡部進一", the library artist is "Shinichi Atobe")."""
    return artist.name if credited_name == artist.original_name else credited_name


@dataclass(frozen=True)
class ArtistNames:
    name: str
    sort_name: str
    # The MusicBrainz name, when ``name`` is an alias.
    original_name: str | None = None


def artist_names(mb_artist: MBArtist, musicbrainz: MusicBrainz | None) -> ArtistNames:
    """The names a MusicBrainz artist gets in the library: its MusicBrainz name if that
    is in Latin script, else its Latin-script alias (``latin_alias``: "Shinichi Atobe"
    for "跡部進一"), with the MusicBrainz name as the original name. The aliases are the
    artist's own (a search or lookup result) or, without them, asked from
    ``musicbrainz`` (one request); no alias, or no answer, keeps the MusicBrainz name."""
    keep = ArtistNames(mb_artist.name, mb_artist.sort_name)
    if is_latin(mb_artist.name):
        return keep
    aliases = mb_artist.aliases
    if not aliases and musicbrainz is not None:
        try:
            aliases = musicbrainz.get_artist(mb_artist.id).aliases
        except (MusicBrainzError, ValidationError) as e:
            logger.warning("aliases of MusicBrainz artist %s: %s", mb_artist.id, e)
    if (alias := latin_alias(aliases)) is None:
        return keep
    sort_name = mb_artist.sort_name
    if not is_latin(sort_name):
        sort_name = alias.sort_name or alias.name
    return ArtistNames(alias.name, sort_name, mb_artist.name)


class _ArtistResolver:
    """The library artists for the MusicBrainz artists of one import (see the module
    docstring); a library artist without MBID is attached to one MusicBrainz artist only."""

    def __init__(
        self,
        session: Session,
        given: Mapping[uuid.UUID, Artist | None],
        musicbrainz: MusicBrainz | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.session = session
        self.given = given
        self.musicbrainz = musicbrainz
        self.log = log or (lambda message: logger.info("%s", message))
        # Artists ``_fill_latin_name`` looked at (one aliases lookup each at most).
        self.named: set[uuid.UUID] = set()
        self.resolved: dict[uuid.UUID, Artist] = {}

    def credit(self, parts: Sequence[MBArtistCreditPart]) -> ArtistCredit:
        credit = []
        for part in parts:
            artist = self.artist(part.artist)
            credit.append(CreditPart(artist, credited_as(artist, part.name), part.joinphrase))
        return get_or_create_artist_credit(self.session, credit)

    def artist(self, mb_artist: MBArtist) -> Artist:
        if (artist := self.resolved.get(mb_artist.id)) is not None:
            return artist
        artist = (
            self.given.get(mb_artist.id)
            or self.session.exec(select(Artist).where(Artist.mbid == mb_artist.id)).first()
        )
        if artist is None:
            artist = self._match_or_create(mb_artist)
        elif artist.mbid is None:
            artist.mbid = mb_artist.id
        elif mb_artist.id not in self.given:
            self._fill_latin_name(artist, mb_artist)
        self.resolved[mb_artist.id] = artist
        return artist

    def fill_latin_names(self, mb_artists: Iterable[MBArtist]) -> None:
        """``fill_latin_name`` for the library artists with these MBIDs (not given: the
        caller decides about those), whether the import credits them or not."""
        wanted = {a.id: a for a in mb_artists if a.id not in self.given}
        if not wanted:
            return
        rows = self.session.exec(select(Artist).where(col(Artist.mbid).in_(wanted)))
        for artist in rows:
            assert artist.mbid is not None
            self._fill_latin_name(artist, wanted[artist.mbid])

    def _fill_latin_name(self, artist: Artist, mb_artist: MBArtist) -> None:
        if mb_artist.id in self.named or not needs_latin_name(artist, mb_artist.name):
            return
        self.named.add(mb_artist.id)
        old = artist.name
        if fill_latin_name(artist, artist_names(mb_artist, self.musicbrainz)):
            self.log(f"artist {old!r} renamed to {artist.name!r}")

    def _match_or_create(self, mb_artist: MBArtist) -> Artist:
        """A confident match without MBID (which gains it; not for a given artist), else
        a new artist."""
        names = artist_names(mb_artist, self.musicbrainz)
        if mb_artist.id not in self.given:
            match = match_artist_names(self.session, names, mb_artist.id).match
            if match is not None and match not in self.resolved.values():
                match.mbid = mb_artist.id
                return match
        artist = Artist(
            mbid=mb_artist.id,
            name=names.name,
            sort_name=names.sort_name,
            original_name=names.original_name,
            disambiguation=mb_artist.disambiguation,
            type=artist_type(mb_artist),
        )
        self.session.add(artist)
        return artist


def match_artist_names(
    session: Session, names: ArtistNames, mbid: uuid.UUID | None
) -> MatchResult[Artist]:
    """``match_artist`` by the name, else (no candidate) by the original name."""
    result = match_artist(session, names.name, mbid=mbid)
    if result.decision is Decision.new and names.original_name:
        return match_artist(session, names.original_name, mbid=mbid)
    return result


def import_artist(
    session: Session, mb_artist: MBArtist, musicbrainz: MusicBrainz | None = None
) -> Artist:
    """The library artist for a MusicBrainz artist: the row with its MBID, a confident
    match without MBID (which gains it), or a new artist (named as ``artist_names``
    says); a library artist still named in another script by MusicBrainz is renamed
    (``fill_latin_name``). Flushes nothing."""
    return _ArtistResolver(session, {}, musicbrainz).artist(mb_artist)
