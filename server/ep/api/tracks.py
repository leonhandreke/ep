import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session

from ep.api.deps import SessionDep, commit, get_or_404
from ep.api.schemas import TrackCreate, TrackRead, TrackUpdate
from ep.models import Recording, ReleaseGroup, Track

router = APIRouter(prefix="/tracks", tags=["tracks"])


def _require(session: Session, model: type[Recording | ReleaseGroup], id: uuid.UUID) -> None:
    if session.get(model, id) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{model.__tablename__} {id} not found"
        )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_track(body: TrackCreate, session: SessionDep) -> TrackRead:
    _require(session, ReleaseGroup, body.release_group_id)
    _require(session, Recording, body.recording_id)
    track = Track.model_validate(body)
    session.add(track)
    commit(session)
    return TrackRead.model_validate(track)


@router.get("/{id}")
def get_track(id: uuid.UUID, session: SessionDep) -> TrackRead:
    return TrackRead.model_validate(get_or_404(session, Track, id))


@router.patch("/{id}")
def update_track(id: uuid.UUID, body: TrackUpdate, session: SessionDep) -> TrackRead:
    track = get_or_404(session, Track, id)
    data = body.model_dump()
    if "recording_id" in data:
        _require(session, Recording, data["recording_id"])
    track.sqlmodel_update(data)
    commit(session)
    return TrackRead.model_validate(track)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_track(id: uuid.UUID, session: SessionDep) -> None:
    session.delete(get_or_404(session, Track, id))
    commit(session)
