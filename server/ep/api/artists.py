import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, select

from ep.api.deps import PageDep, SessionDep, commit, get_or_404
from ep.api.external_ids import add_external_id_routes, delete_external_ids
from ep.api.release_groups import release_group_order
from ep.api.schemas import ArtistCreate, ArtistRead, ArtistUpdate, ReleaseGroupRead
from ep.models import Artist, ArtistCreditName, EntityType, ReleaseGroup

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("")
def list_artists(session: SessionDep, page: PageDep) -> list[ArtistRead]:
    artists = session.exec(
        select(Artist)
        .order_by(col(Artist.sort_name), col(Artist.name), col(Artist.id))
        .limit(page.limit)
        .offset(page.offset)
    )
    return [ArtistRead.model_validate(artist) for artist in artists]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_artist(body: ArtistCreate, session: SessionDep) -> ArtistRead:
    artist = Artist.model_validate(body, update={"sort_name": body.sort_name or body.name})
    session.add(artist)
    commit(session)
    return ArtistRead.model_validate(artist)


@router.get("/{id}")
def get_artist(id: uuid.UUID, session: SessionDep) -> ArtistRead:
    return ArtistRead.model_validate(get_or_404(session, Artist, id))


@router.patch("/{id}")
def update_artist(id: uuid.UUID, body: ArtistUpdate, session: SessionDep) -> ArtistRead:
    artist = get_or_404(session, Artist, id)
    artist.sqlmodel_update(body.model_dump())
    commit(session)
    return ArtistRead.model_validate(artist)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artist(id: uuid.UUID, session: SessionDep) -> None:
    """Delete an artist that is not credited on anything."""
    artist = get_or_404(session, Artist, id)
    if session.exec(select(ArtistCreditName.id).where(ArtistCreditName.artist_id == id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "artist is still credited")
    delete_external_ids(session, EntityType.artist, id)
    session.delete(artist)
    commit(session)


@router.get("/{id}/release-groups")
def list_artist_release_groups(
    id: uuid.UUID, session: SessionDep, page: PageDep
) -> list[ReleaseGroupRead]:
    """Release groups whose artist credit includes this artist, oldest first."""
    get_or_404(session, Artist, id)
    credit_ids = select(ArtistCreditName.artist_credit_id).where(ArtistCreditName.artist_id == id)
    release_groups = session.exec(
        select(ReleaseGroup)
        .where(col(ReleaseGroup.artist_credit_id).in_(credit_ids))
        .order_by(*release_group_order)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [ReleaseGroupRead.model_validate(rg) for rg in release_groups]


add_external_id_routes(router, Artist, EntityType.artist, "")
