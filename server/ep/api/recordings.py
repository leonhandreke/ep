import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING

from ep.api.deps import SessionDep, artist_credit_from_input, commit, get_or_404
from ep.api.external_ids import add_external_id_routes, delete_external_ids
from ep.api.schemas import RecordingCreate, RecordingDetail, RecordingRead, RecordingUpdate
from ep.credits import delete_artist_credit_if_unused
from ep.models import EntityType, Recording

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_recording(body: RecordingCreate, session: SessionDep) -> RecordingRead:
    credit = artist_credit_from_input(session, body.artist_credit)
    recording = Recording.model_validate(
        body.model_dump(exclude={"artist_credit"}), update={"artist_credit_id": credit.id}
    )
    session.add(recording)
    commit(session)
    return RecordingRead.model_validate(recording)


@router.get("/{id}")
def get_recording(id: uuid.UUID, session: SessionDep) -> RecordingDetail:
    """A recording with the tracks (and their release groups) it appears as."""
    return RecordingDetail.model_validate(get_or_404(session, Recording, id))


@router.patch("/{id}")
def update_recording(id: uuid.UUID, body: RecordingUpdate, session: SessionDep) -> RecordingRead:
    recording = get_or_404(session, Recording, id)
    recording.sqlmodel_update(body.model_dump(exclude={"artist_credit"}))
    if body.artist_credit is not MISSING:
        old_credit = recording.artist_credit
        recording.artist_credit = artist_credit_from_input(session, body.artist_credit)
        delete_artist_credit_if_unused(session, old_credit)
    commit(session)
    return RecordingRead.model_validate(recording)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a recording that is not a track of any release group."""
    recording = get_or_404(session, Recording, id)
    if recording.tracks:
        raise HTTPException(status.HTTP_409_CONFLICT, "recording is still a track")
    credit = recording.artist_credit
    delete_external_ids(session, EntityType.recording, id)
    session.delete(recording)
    delete_artist_credit_if_unused(session, credit)
    commit(session)


add_external_id_routes(router, Recording, EntityType.recording, "")
