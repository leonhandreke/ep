"""Catalogue rows from a MusicBrainz release (metadata only: no files, no cover).

``import_release`` adds the release's artists, artist credits, its release group (with
``release_mbid`` set to this release), recordings, tracks and recording ISRCs, reusing
rows that already have the MBID. Existing rows are not updated: edits made in EP win.

One release per release group: if the release group exists with another release, this
raises ``ReleaseConflict``. Importing the same release again adds what is missing.
"""

import uuid
from collections.abc import Sequence

from sqlmodel import Session, col, select

from ep.credits import CreditPart, get_or_create_artist_credit
from ep.metadata.musicbrainz import (
    MBArtist,
    MBArtistCreditPart,
    MBRecording,
    MBRelease,
    MBReleaseGroup,
    normalize_isrc,
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


def import_release(session: Session, release: MBRelease) -> ReleaseGroup:
    """Add a release (fetched with ``MusicBrainz.get_release``) to the catalogue.

    Flushes but does not commit. Returns the release group.
    """
    if release.release_group is None:
        raise ValueError("release lookup without its release group (inc=release-groups)")
    release_group = _release_group(session, release, release.release_group)
    existing = {(track.disc, track.position) for track in release_group.tracks}
    for disc_index, medium in enumerate(release.media, start=1):
        disc = medium.position or disc_index
        for mb_track in medium.tracks:
            if (disc, mb_track.position) in existing:
                continue
            recording = _recording(session, mb_track.recording, mb_track.artist_credit)
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


def _release_group(
    session: Session, release: MBRelease, mb_release_group: MBReleaseGroup
) -> ReleaseGroup:
    release_group = session.exec(
        select(ReleaseGroup).where(ReleaseGroup.mbid == mb_release_group.id)
    ).first()
    if release_group is not None:
        if release_group.release_mbid not in (None, release.id):
            raise ReleaseConflict(
                f"release group {mb_release_group.id} ({release_group.title!r}) is already "
                f"imported from release {release_group.release_mbid}, not {release.id}"
            )
        release_group.release_mbid = release.id
        return release_group
    credit = _artist_credit(session, mb_release_group.artist_credit or release.artist_credit)
    release_group = ReleaseGroup(
        mbid=mb_release_group.id,
        release_mbid=release.id,
        title=mb_release_group.title,
        artist_credit_id=credit.id,
        type=_release_group_type(mb_release_group),
        date=mb_release_group.first_release_date or release.date,
        barcode=release.barcode,
    )
    session.add(release_group)
    return release_group


def _release_group_type(release_group: MBReleaseGroup) -> ReleaseGroupType:
    if "Compilation" in release_group.secondary_types:
        return ReleaseGroupType.compilation
    return _PRIMARY_TYPES.get(release_group.primary_type or "", ReleaseGroupType.other)


def _recording(
    session: Session, mb_recording: MBRecording, track_credit: Sequence[MBArtistCreditPart]
) -> Recording:
    recording = session.exec(select(Recording).where(Recording.mbid == mb_recording.id)).first()
    if recording is None:
        credit = _artist_credit(session, mb_recording.artist_credit or track_credit)
        recording = Recording(
            mbid=mb_recording.id,
            title=mb_recording.title,
            artist_credit_id=credit.id,
            length_ms=mb_recording.length,
        )
        session.add(recording)
    _add_isrcs(session, recording.id, mb_recording.isrcs)
    return recording


def _add_isrcs(session: Session, recording_id: uuid.UUID, isrcs: Sequence[str]) -> None:
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


def _artist_credit(session: Session, parts: Sequence[MBArtistCreditPart]) -> ArtistCredit:
    return get_or_create_artist_credit(
        session,
        [CreditPart(_artist(session, part.artist), part.name, part.joinphrase) for part in parts],
    )


def _artist(session: Session, mb_artist: MBArtist) -> Artist:
    artist = session.exec(select(Artist).where(Artist.mbid == mb_artist.id)).first()
    if artist is None:
        artist = Artist(
            mbid=mb_artist.id,
            name=mb_artist.name,
            sort_name=mb_artist.sort_name,
            disambiguation=mb_artist.disambiguation,
            type=_ARTIST_TYPES.get(mb_artist.type, ArtistType.other) if mb_artist.type else None,
        )
        session.add(artist)
    return artist
