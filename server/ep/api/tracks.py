import uuid
from collections.abc import Sequence

from fastapi import APIRouter, status
from sqlmodel import Session, func, select

from ep.api.deps import SessionDep, artist_credit_from_input, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.schemas import (
    LinkRead,
    PlayabilityRead,
    PlaybackRead,
    RadioEntryRefRead,
    RecordingRead,
    TrackCreate,
    TrackRead,
    TrackUpdate,
)
from ep.credits import with_credit
from ep.models import Recording, ReleaseGroup, Track
from ep.playback import Playability, Playback, playabilities

router = APIRouter(prefix="/tracks", tags=["tracks"])

# Load option for track lists: what ``TrackRead`` shows of each track's recording.
with_recording = with_credit(Track.recording, Recording.artist_credit)


def playback_read(playback: Playback | None) -> PlaybackRead | None:
    return PlaybackRead.model_validate(playback) if playback is not None else None


def playability_read(playability: Playability) -> PlayabilityRead:
    link = playability.link
    radio = playability.radio
    return PlayabilityRead(
        state=playability.state,
        playback=playback_read(playability.playback),
        radio_entry=(
            RadioEntryRefRead(track_id=radio.track_id, episode_id=radio.episode_id)
            if radio is not None
            else None
        ),
        link=(
            LinkRead(source_id=link.id, kind=link.kind, url=link.uri or "")
            if link is not None
            else None
        ),
        missing_file_source_id=playability.missing_file_source_id,
    )


def track_reads(session: Session, tracks: Sequence[Track]) -> list[TrackRead]:
    """Tracks with how each plays (one query for all; load the tracks ``with_recording``)."""
    found = playabilities(
        session, {track.recording.id: track.recording for track in tracks}.values()
    )
    return [_track_read(track, found[track.recording_id]) for track in tracks]


def _track_read(track: Track, playability: Playability) -> TrackRead:
    return TrackRead(
        id=track.id,
        created_at=track.created_at,
        updated_at=track.updated_at,
        release_group_id=track.release_group_id,
        disc=track.disc,
        position=track.position,
        title=track.title,
        recording=RecordingRead.model_validate(track.recording),
        playability=playability_read(playability),
    )


def track_read(session: Session, track: Track) -> TrackRead:
    return track_reads(session, [track])[0]


def next_position(session: Session, release_group_id: uuid.UUID, disc: int) -> int:
    last = session.exec(
        select(func.max(Track.position)).where(
            Track.release_group_id == release_group_id, Track.disc == disc
        )
    ).one()
    return (last or 0) + 1


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_track(body: TrackCreate, session: SessionDep) -> TrackRead:
    """Add a track to a release group: an existing recording, or a new one created with
    it (credited like the release group unless a credit is given). Without a
    position it goes after the last track of its disc."""
    release_group = get_or_422(session, ReleaseGroup, body.release_group_id, ("release_group_id",))
    if body.recording_id is not None:
        recording = get_or_422(session, Recording, body.recording_id, ("recording_id",))
    else:
        assert body.new_recording is not None
        new = body.new_recording
        credit = (
            artist_credit_from_input(session, new.artist_credit, ("new_recording", "artist_credit"))
            if new.artist_credit is not None
            else release_group.artist_credit
        )
        recording = Recording(title=new.title, length_ms=new.length_ms, artist_credit_id=credit.id)
        session.add(recording)
    position = body.position
    if position is None:
        position = next_position(session, release_group.id, body.disc)
    track = Track(
        release_group_id=release_group.id,
        recording_id=recording.id,
        disc=body.disc,
        position=position,
        title=body.title,
    )
    session.add(track)
    session.commit()
    return track_read(session, track)


@router.get("/{id}", responses=NOT_FOUND)
def get_track(id: uuid.UUID, session: SessionDep) -> TrackRead:
    return track_read(session, get_or_404(session, Track, id))


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_track(id: uuid.UUID, body: TrackUpdate, session: SessionDep) -> TrackRead:
    track = get_or_404(session, Track, id)
    data = body.model_dump()
    if "recording_id" in data:
        get_or_422(session, Recording, data["recording_id"], ("recording_id",))
    track.sqlmodel_update(data)
    session.commit()
    return track_read(session, track)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_track(id: uuid.UUID, session: SessionDep) -> None:
    session.delete(get_or_404(session, Track, id))
    session.commit()
