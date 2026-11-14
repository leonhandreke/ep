import uuid
from collections.abc import Sequence

from fastapi import APIRouter, status
from sqlmodel import Session

from ep.api.deps import SessionDep, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.schemas import PlaybackRead, TrackCreate, TrackRead, TrackUpdate
from ep.credits import with_credit
from ep.models import Recording, ReleaseGroup, Track
from ep.playback import Playback, recording_playbacks

router = APIRouter(prefix="/tracks", tags=["tracks"])

# Load option for track lists: what ``TrackRead`` shows of each track's recording.
with_recording = with_credit(Track.recording, Recording.artist_credit)


def track_reads(session: Session, tracks: Sequence[Track]) -> list[TrackRead]:
    """Tracks with how to play each (its recording's first playable source).

    One query for all playbacks; load the tracks ``with_recording``.
    """
    playbacks = recording_playbacks(session, {track.recording_id for track in tracks})
    return [_track_read(track, playbacks.get(track.recording_id)) for track in tracks]


def _track_read(track: Track, playback: Playback | None) -> TrackRead:
    return TrackRead.model_validate(track).model_copy(
        update={"playback": playback and PlaybackRead.model_validate(playback)}
    )


def track_read(session: Session, track: Track) -> TrackRead:
    return track_reads(session, [track])[0]


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_track(body: TrackCreate, session: SessionDep) -> TrackRead:
    get_or_422(session, ReleaseGroup, body.release_group_id, ("release_group_id",))
    get_or_422(session, Recording, body.recording_id, ("recording_id",))
    track = Track.model_validate(body)
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
