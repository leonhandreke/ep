import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import MISSING
from sqlalchemy.orm import contains_eager
from sqlmodel import col, select

from ep.api.deps import SessionDep, artist_credit_from_input, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.external_ids import add_external_id_routes
from ep.api.release_groups import release_group_order
from ep.api.schemas import (
    RecordingCreate,
    RecordingDetail,
    RecordingRead,
    RecordingUpdate,
    SourceRead,
)
from ep.api.sources import source_read
from ep.credits import delete_artist_credit_if_unused, with_credit
from ep.external_ids import delete_external_ids
from ep.models import ArtistCredit, EntityType, Recording, ReleaseGroup, Source, Track, rel
from ep.notes.store import delete_annotations
from ep.playback import resolve_where

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT)
def create_recording(body: RecordingCreate, session: SessionDep) -> RecordingRead:
    credit = artist_credit_from_input(session, body.artist_credit)
    recording = Recording.model_validate(
        body.model_dump(exclude={"artist_credit"}), update={"artist_credit_id": credit.id}
    )
    session.add(recording)
    session.commit()
    return RecordingRead.model_validate(recording)


@router.get("/{id}", responses=NOT_FOUND)
def get_recording(id: uuid.UUID, session: SessionDep) -> RecordingDetail:
    """A recording with the tracks (and their release groups) it appears as.

    Tracks by release group (oldest first, as on an artist), then disc and position.
    """
    recording = (
        session.exec(
            select(Recording)
            .where(Recording.id == id)
            .outerjoin(rel(Recording.tracks))
            .outerjoin(rel(Track.release_group))
            .order_by(*release_group_order, col(Track.disc), col(Track.position))
            .options(
                with_credit(Recording.artist_credit),
                contains_eager(rel(Recording.tracks))
                .contains_eager(rel(Track.release_group))
                .joinedload(rel(ReleaseGroup.artist_credit))
                .selectinload(rel(ArtistCredit.names)),
            )
        )
        .unique()
        .one_or_none()
    )
    if recording is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"recording {id} not found")
    return RecordingDetail.model_validate(recording)


@router.patch("/{id}", responses=NOT_FOUND | CONFLICT)
def update_recording(id: uuid.UUID, body: RecordingUpdate, session: SessionDep) -> RecordingRead:
    recording = get_or_404(session, Recording, id)
    recording.sqlmodel_update(body.model_dump(exclude={"artist_credit"}))
    if body.artist_credit is not MISSING:
        old_credit = recording.artist_credit
        recording.artist_credit = artist_credit_from_input(session, body.artist_credit)
        delete_artist_credit_if_unused(session, old_credit)
    session.commit()
    return RecordingRead.model_validate(recording)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND | CONFLICT)
def delete_recording(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a recording that is not a track of any release group and has no sources."""
    recording = get_or_404(session, Recording, id)
    if recording.tracks:
        raise HTTPException(status.HTTP_409_CONFLICT, "recording is still a track")
    if session.exec(select(Source.id).where(Source.recording_id == id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "recording still has sources")
    credit = recording.artist_credit
    delete_external_ids(session, EntityType.recording, id)
    delete_annotations(session, EntityType.recording, id)
    session.delete(recording)
    delete_artist_credit_if_unused(session, credit)
    session.commit()


@router.get("/{id}/sources", responses=NOT_FOUND)
def list_recording_sources(id: uuid.UUID, session: SessionDep) -> list[SourceRead]:
    """Sources that play this recording, oldest first, each with how to play it."""
    get_or_404(session, Recording, id)
    resolved = resolve_where(session, col(Source.recording_id) == id)
    return [source_read(source, playback) for source, playback in resolved]


add_external_id_routes(router, Recording, EntityType.recording, "")
