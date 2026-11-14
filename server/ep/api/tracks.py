import uuid

from fastapi import APIRouter, status

from ep.api.deps import SessionDep, get_or_404, get_or_422
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.schemas import TrackCreate, TrackRead, TrackUpdate
from ep.models import Recording, ReleaseGroup, Track

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_track(body: TrackCreate, session: SessionDep) -> TrackRead:
    get_or_422(session, ReleaseGroup, body.release_group_id, ("release_group_id",))
    get_or_422(session, Recording, body.recording_id, ("recording_id",))
    track = Track.model_validate(body)
    session.add(track)
    session.commit()
    return TrackRead.model_validate(track)


@router.get("/{id}", responses=NOT_FOUND)
def get_track(id: uuid.UUID, session: SessionDep) -> TrackRead:
    return TrackRead.model_validate(get_or_404(session, Track, id))


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_track(id: uuid.UUID, body: TrackUpdate, session: SessionDep) -> TrackRead:
    track = get_or_404(session, Track, id)
    data = body.model_dump()
    if "recording_id" in data:
        get_or_422(session, Recording, data["recording_id"], ("recording_id",))
    track.sqlmodel_update(data)
    session.commit()
    return TrackRead.model_validate(track)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_track(id: uuid.UUID, session: SessionDep) -> None:
    session.delete(get_or_404(session, Track, id))
    session.commit()
