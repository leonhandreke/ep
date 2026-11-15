"""Radio shows, episodes and their tracklists (imported from NTS, ``ep.importers.nts``).

Episodes list their tracklist with the station's text, the time range of each entry
within the episode, the recording it was matched to (if any), whether the library has
that recording other than from radio, and how to play the entry and the whole episode.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING
from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import contains_eager, joinedload, selectinload
from sqlmodel import Session, col, select

from ep.api.deps import PageDep, SessionDep, SettingsDep, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.schemas import (
    PlaybackRead,
    RadioAppearanceRead,
    RadioEpisodeDetail,
    RadioEpisodeRead,
    RadioEpisodeTrackRead,
    RadioEpisodeTrackUpdate,
    RadioEpisodeUpdate,
    RadioShowRead,
    RadioShowUpdate,
    RecordingRead,
)
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistCreditName,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    Recording,
    Source,
    SourceKind,
    WatchDir,
    rel,
)
from ep.playback import Playback, resolve_where_flagged
from ep.radio import delete_episode, delete_show, delete_unused_recordings, radio_only_ids
from ep.settings import Settings

router = APIRouter(prefix="/radio", tags=["radio"])
# ``/recordings/{id}/radio-appearances`` and ``/artists/{id}/radio-appearances``.
appearances_router = APIRouter(tags=["radio"])

_episode_order = (
    col(RadioEpisode.broadcast_at).desc().nulls_last(),
    col(RadioEpisode.name),
    col(RadioEpisode.id),
)


def _playback(playback: Playback | None) -> PlaybackRead | None:
    return PlaybackRead.model_validate(playback) if playback is not None else None


def _remove_files(files: Sequence[Path]) -> None:
    for path in files:
        path.unlink(missing_ok=True)


def _protected(session: Session, settings: Settings) -> list[Path]:
    """Directories whose files are never deleted: the watched directories and every
    media root other than the managed library."""
    library = settings.library_dir.resolve()
    roots = [root for root in settings.media_roots if root.resolve() != library]
    return [*roots, *(Path(path) for path in session.exec(select(WatchDir.path)).all())]


# Shows


@router.get("/shows")
def list_radio_shows(session: SessionDep, page: PageDep) -> list[RadioShowRead]:
    """Radio shows, by name."""
    shows = session.exec(
        select(RadioShow)
        .order_by(col(RadioShow.name), col(RadioShow.id))
        .limit(page.limit)
        .offset(page.offset)
    )
    return [RadioShowRead.model_validate(show) for show in shows]


@router.get("/shows/{id}", responses=NOT_FOUND)
def get_radio_show(id: uuid.UUID, session: SessionDep) -> RadioShowRead:
    return RadioShowRead.model_validate(get_or_404(session, RadioShow, id))


@router.patch("/shows/{id}", responses=NOT_FOUND | CONFLICT)
def update_radio_show(id: uuid.UUID, body: RadioShowUpdate, session: SessionDep) -> RadioShowRead:
    show = get_or_404(session, RadioShow, id)
    show.sqlmodel_update(body.model_dump())
    session.commit()
    return RadioShowRead.model_validate(show)


@router.delete("/shows/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_radio_show(
    id: uuid.UUID, session: SessionDep, settings: SettingsDep, delete_audio: bool = False
) -> None:
    """Delete a show with its episodes (see ``DELETE /radio/episodes/{id}``)."""
    show = get_or_404(session, RadioShow, id)
    files = delete_show(
        session, show, settings.library_dir, delete_audio, _protected(session, settings)
    )
    session.commit()
    _remove_files(files)


@router.get("/shows/{id}/episodes", responses=NOT_FOUND)
def list_radio_show_episodes(
    id: uuid.UUID, session: SessionDep, page: PageDep
) -> list[RadioEpisodeRead]:
    """A show's episodes, newest first."""
    get_or_404(session, RadioShow, id)
    episodes = session.exec(
        select(RadioEpisode)
        .where(RadioEpisode.show_id == id)
        .order_by(*_episode_order)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [RadioEpisodeRead.model_validate(episode) for episode in episodes]


# Episodes


def _load_episode(session: Session, id: uuid.UUID) -> RadioEpisode:
    episode = (
        session.exec(
            select(RadioEpisode)
            .where(RadioEpisode.id == id)
            .options(
                joinedload(rel(RadioEpisode.show)),
                selectinload(rel(RadioEpisode.tracks))
                .joinedload(rel(RadioEpisodeTrack.recording))
                .joinedload(rel(Recording.artist_credit))
                .selectinload(rel(ArtistCredit.names)),
                selectinload(rel(RadioEpisode.tracks)).joinedload(rel(RadioEpisodeTrack.source)),
                joinedload(rel(RadioEpisode.source)),
            )
        )
        .unique()
        .one_or_none()
    )
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"radio_episode {id} not found")
    return episode


class _Playbacks:
    """Playback for tracklist entries (their slices, their recordings in the library)
    and the episode file, resolved in one query."""

    def __init__(
        self,
        session: Session,
        tracks: Sequence[RadioEpisodeTrack],
        episode_source: uuid.UUID | None,
    ) -> None:
        recording_ids = {t.recording_id for t in tracks if t.recording_id is not None}
        source_ids = {t.source_id for t in tracks if t.source_id is not None}
        if episode_source is not None:
            source_ids.add(episode_source)
        conditions: list[ColumnElement[bool]] = []
        if recording_ids:
            conditions.append(col(Source.recording_id).in_(recording_ids))
        if source_ids:
            conditions.append(col(Source.id).in_(source_ids))
        self.by_source: dict[uuid.UUID, Playback] = {}
        self.library: dict[uuid.UUID, Playback] = {}
        if not conditions:
            return
        for source, playback, radio in resolve_where_flagged(session, or_(*conditions)):
            if playback is None:
                continue
            self.by_source[source.id] = playback
            if not radio and source.recording_id is not None:
                self.library.setdefault(source.recording_id, playback)

    def track(self, track: RadioEpisodeTrack) -> RadioEpisodeTrackRead:
        source = track.source
        library = self.library.get(track.recording_id) if track.recording_id else None
        return RadioEpisodeTrackRead(
            id=track.id,
            created_at=track.created_at,
            updated_at=track.updated_at,
            episode_id=track.episode_id,
            position=track.position,
            artist_text=track.artist_text,
            title_text=track.title_text,
            start_ms=source.start_ms if source is not None else None,
            end_ms=source.end_ms if source is not None else None,
            offset_is_estimate=track.offset_is_estimate,
            source_id=track.source_id,
            recording=(
                RecordingRead.model_validate(track.recording)
                if track.recording is not None
                else None
            ),
            in_library=library is not None,
            library_playback=_playback(library),
            playback=_playback(self.by_source.get(track.source_id) if track.source_id else None),
        )


@router.get("/episodes/{id}", responses=NOT_FOUND)
def get_radio_episode(id: uuid.UUID, session: SessionDep) -> RadioEpisodeDetail:
    """An episode with its show and its tracklist, in order."""
    episode = _load_episode(session, id)
    playbacks = _Playbacks(session, episode.tracks, episode.source_id)
    return RadioEpisodeDetail(
        **RadioEpisodeRead.model_validate(episode).model_dump(),
        image_path=episode.image_path,
        show=RadioShowRead.model_validate(episode.show),
        playback=_playback(
            playbacks.by_source.get(episode.source_id) if episode.source_id else None
        ),
        duration_ms=episode.source.duration_ms if episode.source is not None else None,
        tracks=[playbacks.track(track) for track in episode.tracks],
    )


@router.patch("/episodes/{id}", responses=NOT_FOUND | CONFLICT)
def update_radio_episode(
    id: uuid.UUID, body: RadioEpisodeUpdate, session: SessionDep
) -> RadioEpisodeRead:
    episode = get_or_404(session, RadioEpisode, id)
    episode.sqlmodel_update(body.model_dump())
    session.commit()
    return RadioEpisodeRead.model_validate(episode)


@router.delete("/episodes/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_radio_episode(
    id: uuid.UUID, session: SessionDep, settings: SettingsDep, delete_audio: bool = False
) -> None:
    """Delete an episode with its tracklist, its slices and episode file source, and the
    recordings (with their artists) that only this episode knew. Recordings that are in
    the library otherwise keep everything but their slice. The audio file stays unless
    ``delete_audio`` (only a file EP downloaded into the managed library is deleted)."""
    episode = get_or_404(session, RadioEpisode, id)
    files = delete_episode(
        session, episode, settings.library_dir, delete_audio, _protected(session, settings)
    )
    session.commit()
    _remove_files(files)


@router.patch("/episode-tracks/{id}", responses=NOT_FOUND)
def update_radio_episode_track(
    id: uuid.UUID, body: RadioEpisodeTrackUpdate, session: SessionDep
) -> RadioEpisodeTrackRead:
    """Re-point a tracklist entry to another recording (or to none) and/or adjust its
    time range within the episode. The entry's slice follows: it plays the new
    recording, and its range is the entry's. An entry without a slice gets one when a
    range is given (if the episode has a file). The previous recording is deleted if it
    was only known from radio and no entry plays it any more."""
    track = session.get(RadioEpisodeTrack, id)
    if track is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"radio_episode_track {id} not found")
    replaced: set[uuid.UUID] = set()
    if body.recording_id is not MISSING and body.recording_id != track.recording_id:
        if body.recording_id is not None:
            get_or_422(session, Recording, body.recording_id, ("recording_id",))
        if track.recording_id is not None:
            replaced = radio_only_ids(session, [track.recording_id])
        track.recording_id = body.recording_id
        if track.source is not None:
            track.source.recording_id = body.recording_id
    if body.start_ms is not MISSING or body.end_ms is not MISSING:
        slice_ = track.source or _new_slice(session, track, body.start_ms)
        if body.start_ms is not MISSING:
            slice_.start_ms = body.start_ms
        if body.end_ms is not MISSING:
            slice_.end_ms = body.end_ms
        if slice_.end_ms is not None and slice_.end_ms <= (slice_.start_ms or 0):
            raise unprocessable(("end_ms",), "must be after start_ms", slice_.end_ms, "value_error")
        slice_.duration_ms = slice_.end_ms - (slice_.start_ms or 0) if slice_.end_ms else None
        if body.offset_is_estimate is MISSING:
            track.offset_is_estimate = False
    if body.offset_is_estimate is not MISSING:
        track.offset_is_estimate = body.offset_is_estimate
    # A radio-only recording that no entry plays any more goes (``ep.radio``).
    delete_unused_recordings(session, replaced)
    session.commit()
    session.refresh(track)
    return _Playbacks(session, [track], None).track(track)


def _new_slice(session: Session, track: RadioEpisodeTrack, start_ms: int | object) -> Source:
    episode = track.episode
    if episode.source_id is None:
        raise unprocessable(
            ("start_ms",), "the episode has no audio file to slice", None, "value_error"
        )
    if start_ms is MISSING:
        raise unprocessable(("start_ms",), "required for an entry without a range", None)
    source = Source(
        kind=SourceKind.slice,
        parent_id=episode.source_id,
        recording_id=track.recording_id,
    )
    session.add(source)
    track.source = source
    return source


# Appearances


def _appearances(session: Session, recordings: ColumnElement[bool]) -> list[RadioAppearanceRead]:
    tracks = (
        session.exec(
            select(RadioEpisodeTrack)
            .join(rel(RadioEpisodeTrack.episode))
            .join(rel(RadioEpisode.show))
            .join(Recording, col(RadioEpisodeTrack.recording_id) == Recording.id)
            .where(recordings)
            .order_by(*_episode_order, col(RadioEpisodeTrack.position))
            .options(
                contains_eager(rel(RadioEpisodeTrack.episode)).contains_eager(
                    rel(RadioEpisode.show)
                ),
                contains_eager(rel(RadioEpisodeTrack.recording))
                .joinedload(rel(Recording.artist_credit))
                .selectinload(rel(ArtistCredit.names)),
                joinedload(rel(RadioEpisodeTrack.source)),
            )
        )
        .unique()
        .all()
    )
    reads = []
    for track in tracks:
        assert track.recording is not None
        source = track.source
        reads.append(
            RadioAppearanceRead(
                track_id=track.id,
                position=track.position,
                start_ms=source.start_ms if source is not None else None,
                end_ms=source.end_ms if source is not None else None,
                offset_is_estimate=track.offset_is_estimate,
                recording=RecordingRead.model_validate(track.recording),
                episode=RadioEpisodeRead.model_validate(track.episode),
                show=RadioShowRead.model_validate(track.episode.show),
            )
        )
    return reads


@appearances_router.get("/recordings/{id}/radio-appearances", responses=NOT_FOUND)
def list_recording_radio_appearances(
    id: uuid.UUID, session: SessionDep
) -> list[RadioAppearanceRead]:
    """The radio episodes that played this recording, newest first, with the time."""
    get_or_404(session, Recording, id)
    return _appearances(session, col(Recording.id) == id)


@appearances_router.get("/artists/{id}/radio-appearances", responses=NOT_FOUND)
def list_artist_radio_appearances(id: uuid.UUID, session: SessionDep) -> list[RadioAppearanceRead]:
    """The radio episodes that played recordings credited to this artist, newest first."""
    get_or_404(session, Artist, id)
    credits = select(ArtistCreditName.artist_credit_id).where(ArtistCreditName.artist_id == id)
    return _appearances(session, col(Recording.artist_credit_id).in_(credits))
