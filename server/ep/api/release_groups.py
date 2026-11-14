import uuid

from fastapi import APIRouter, status
from pydantic import MISSING
from sqlmodel import col, select

from ep.api.deps import PageDep, SessionDep, artist_credit_from_input, commit, get_or_404
from ep.api.external_ids import add_external_id_routes, delete_external_ids
from ep.api.schemas import (
    ReleaseGroupCreate,
    ReleaseGroupRead,
    ReleaseGroupUpdate,
    TrackRead,
)
from ep.credits import delete_artist_credit_if_unused
from ep.models import EntityType, ReleaseGroup, Track

router = APIRouter(prefix="/release-groups", tags=["release groups"])

# Oldest first; undated release groups last. Partial dates sort correctly as text.
release_group_order = (
    col(ReleaseGroup.date).asc().nulls_last(),
    col(ReleaseGroup.title),
    col(ReleaseGroup.id),
)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_release_group(body: ReleaseGroupCreate, session: SessionDep) -> ReleaseGroupRead:
    credit = artist_credit_from_input(session, body.artist_credit)
    release_group = ReleaseGroup.model_validate(
        body.model_dump(exclude={"artist_credit"}), update={"artist_credit_id": credit.id}
    )
    session.add(release_group)
    commit(session)
    return ReleaseGroupRead.model_validate(release_group)


@router.get("/{id}")
def get_release_group(id: uuid.UUID, session: SessionDep) -> ReleaseGroupRead:
    return ReleaseGroupRead.model_validate(get_or_404(session, ReleaseGroup, id))


@router.patch("/{id}")
def update_release_group(
    id: uuid.UUID, body: ReleaseGroupUpdate, session: SessionDep
) -> ReleaseGroupRead:
    release_group = get_or_404(session, ReleaseGroup, id)
    release_group.sqlmodel_update(body.model_dump(exclude={"artist_credit"}))
    if body.artist_credit is not MISSING:
        old_credit = release_group.artist_credit
        release_group.artist_credit = artist_credit_from_input(session, body.artist_credit)
        delete_artist_credit_if_unused(session, old_credit)
    commit(session)
    return ReleaseGroupRead.model_validate(release_group)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_release_group(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a release group and its tracks. The recordings stay."""
    release_group = get_or_404(session, ReleaseGroup, id)
    credit = release_group.artist_credit
    delete_external_ids(session, EntityType.release_group, id)
    session.delete(release_group)
    delete_artist_credit_if_unused(session, credit)
    commit(session)


@router.get("/{id}/tracks")
def list_release_group_tracks(id: uuid.UUID, session: SessionDep, page: PageDep) -> list[TrackRead]:
    """Tracks in disc and position order, each with its recording."""
    get_or_404(session, ReleaseGroup, id)
    tracks = session.exec(
        select(Track)
        .where(Track.release_group_id == id)
        .order_by(col(Track.disc), col(Track.position))
        .limit(page.limit)
        .offset(page.offset)
    )
    return [TrackRead.model_validate(track) for track in tracks]


add_external_id_routes(router, ReleaseGroup, EntityType.release_group, "")
