"""Radio material in the catalogue: what is radio-only, and deleting episodes.

A recording is *radio-only* when it is known only from radio tracklists: it appears in
one (a ``radio_episode_track`` points at it), it is not a track of any release group,
and every one of its sources is a tracklist entry's slice. An artist is radio-only when
it is credited on no release group, is credited on at least one recording and all its
recordings are radio-only. (The "appears in one" and "at least one" conditions keep
hand-made rows without sources from counting as radio-only.) Browse lists and
``/search`` hide radio-only rows unless ``include_radio_only`` is set. Both are SQL
expressions (correlated ``EXISTS``), computed in the query, never stored.
"""

import uuid
from pathlib import Path

from sqlalchemy import ColumnElement, and_, exists
from sqlalchemy.orm import Mapped, aliased
from sqlmodel import Session, col, select

from ep import covers
from ep.credits import delete_artist_credit_if_unused
from ep.external_ids import delete_external_ids
from ep.models import (
    Artist,
    ArtistCreditName,
    EntityType,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
)


def radio_only_recording(
    recording_id: Mapped[uuid.UUID] | ColumnElement[uuid.UUID],
) -> ColumnElement[bool]:
    """SQL: the recording with this id (a column of the enclosing query) is radio-only."""
    radio_slice = aliased(RadioEpisodeTrack)
    in_radio = exists().where(col(RadioEpisodeTrack.recording_id) == recording_id)
    has_track = exists().where(col(Track.recording_id) == recording_id)
    other_source = exists().where(
        col(Source.recording_id) == recording_id,
        ~exists().where(col(radio_slice.source_id) == col(Source.id)),
    )
    return and_(in_radio, ~has_track, ~other_source)


def radio_only_artist(
    artist_id: Mapped[uuid.UUID] | ColumnElement[uuid.UUID],
) -> ColumnElement[bool]:
    """SQL: the artist with this id (a column of the enclosing query) is radio-only."""
    credits = (
        select(ArtistCreditName.artist_credit_id)
        .where(col(ArtistCreditName.artist_id) == artist_id)
        .correlate_except(ArtistCreditName)  # nested in EXISTS: correlate to the artist
    )
    recording = aliased(Recording)
    has_release_group = exists().where(col(ReleaseGroup.artist_credit_id).in_(credits))
    has_recording = exists().where(col(recording.artist_credit_id).in_(credits))
    other_recording = exists().where(
        col(recording.artist_credit_id).in_(credits), ~radio_only_recording(col(recording.id))
    )
    return and_(~has_release_group, has_recording, ~other_recording)


def visible_recordings(include_radio_only: bool) -> ColumnElement[bool] | None:
    """A filter on ``Recording`` for browse lists (None: no filter)."""
    return None if include_radio_only else ~radio_only_recording(col(Recording.id))


def visible_artists(include_radio_only: bool) -> ColumnElement[bool] | None:
    """A filter on ``Artist`` for browse lists (None: no filter)."""
    return None if include_radio_only else ~radio_only_artist(col(Artist.id))


# Deleting


def delete_episode(
    session: Session, episode: RadioEpisode, library_dir: Path, delete_audio: bool = False
) -> list[Path]:
    """Delete an episode with its tracklist, the slices of the episode file, the file
    source and the links it came from (if nothing else derives from them), and the
    recordings only this episode knew (radio-only, in no other episode), with their
    external IDs, credits and artists left uncredited. Recordings in the library
    otherwise only lose their slice. The episode's image is deleted; the audio file
    only with ``delete_audio`` (and only a file EP downloaded into the library).

    Flushes; the caller commits. Returns the audio files to delete after the commit.
    """
    session.flush()
    recording_ids = {t.recording_id for t in episode.tracks if t.recording_id is not None}
    others = select(RadioEpisodeTrack.recording_id).where(
        col(RadioEpisodeTrack.episode_id) != episode.id,
        col(RadioEpisodeTrack.recording_id).in_(recording_ids),
    )
    orphans = list(
        session.exec(
            select(Recording).where(
                col(Recording.id).in_(recording_ids),
                radio_only_recording(col(Recording.id)),
                col(Recording.id).not_in(others),
            )
        ).all()
    )
    slice_ids = {t.source_id for t in episode.tracks if t.source_id is not None}
    file = episode.source
    for track in list(episode.tracks):
        session.delete(track)
    episode.source_id = None
    session.flush()
    session.expire(episode, ["tracks"])  # deleted: not again with the episode
    for source_id in slice_ids:
        if (source := session.get(Source, source_id)) is not None and not source.children:
            session.delete(source)
    session.flush()
    files: list[Path] = []
    if file is not None:
        files = _delete_file_source(session, file, library_dir, delete_audio)
    for recording in orphans:
        _delete_recording(session, recording)
    if episode.image_path:
        covers.remove_cover_file(episode.image_path, library_dir)
    session.delete(episode)
    session.flush()
    return files


def delete_show(
    session: Session, show: RadioShow, library_dir: Path, delete_audio: bool = False
) -> list[Path]:
    """Delete a show and its episodes (``delete_episode``)."""
    files: list[Path] = []
    for episode in list(show.episodes):
        files += delete_episode(session, episode, library_dir, delete_audio)
    if show.image_path:
        covers.remove_cover_file(show.image_path, library_dir)
    session.delete(show)
    session.flush()
    return files


def _delete_file_source(
    session: Session, file: Source, library_dir: Path, delete_audio: bool
) -> list[Path]:
    """The episode file source, unless something still derives from it or plays it
    (a slice made by hand), and then its parent links that nothing else derives from."""
    session.refresh(file)
    if file.children or file.recording_id is not None:
        return []
    used = select(RadioEpisode.id).where(RadioEpisode.source_id == file.id)
    if session.exec(used).first() is not None:
        return []
    path = Path(file.uri or "")
    audio = (
        [path]
        if delete_audio
        and file.kind is SourceKind.file
        and file.managed
        and path.is_relative_to(library_dir)
        else []
    )
    parent = file.parent
    session.delete(file)
    session.flush()
    while parent is not None:
        session.refresh(parent)
        if parent.children or parent.recording_id is not None:
            break
        grandparent = parent.parent
        session.delete(parent)
        session.flush()
        parent = grandparent
    return audio


def _delete_recording(session: Session, recording: Recording) -> None:
    credit = recording.artist_credit
    artist_ids = [name.artist_id for name in credit.names]
    delete_external_ids(session, EntityType.recording, recording.id)
    session.delete(recording)
    delete_artist_credit_if_unused(session, credit)
    session.flush()
    for artist_id in artist_ids:
        credited = select(ArtistCreditName.id).where(ArtistCreditName.artist_id == artist_id)
        if session.exec(credited).first() is None and (artist := session.get(Artist, artist_id)):
            delete_external_ids(session, EntityType.artist, artist_id)
            session.delete(artist)
    session.flush()
