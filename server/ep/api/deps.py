"""Shared helpers for routers."""

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query, Response, status
from sqlalchemy.sql.selectable import GenerativeSelect
from sqlmodel import Session, SQLModel, func, select

from ep.api.errors import unprocessable
from ep.api.schemas import ArtistCreditNameIn, Page
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.db import get_session
from ep.metadata.musicbrainz import MusicBrainz
from ep.metadata.wikidata import Wikidata
from ep.models import Artist, ArtistCredit
from ep.settings import Settings, get_settings

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_musicbrainz() -> Iterator[MusicBrainz]:
    """FastAPI dependency: a MusicBrainz client for the request."""
    with MusicBrainz() as client:
        yield client


def get_wikidata() -> Iterator[Wikidata]:
    """FastAPI dependency: a Wikidata client for the request."""
    with Wikidata() as client:
        yield client


MusicBrainzDep = Annotated[MusicBrainz, Depends(get_musicbrainz)]
WikidataDep = Annotated[Wikidata, Depends(get_wikidata)]

TOTAL_COUNT = "X-Total-Count"
PAGED: dict[int | str, dict[str, Any]] = {
    status.HTTP_200_OK: {
        "headers": {
            TOTAL_COUNT: {
                "description": "Number of items in the whole list (all pages).",
                "required": True,
                "schema": {"type": "integer"},
            }
        }
    }
}
"""``responses=`` of every endpoint that takes ``PageDep``."""


@dataclass
class Paging:
    """``limit``/``offset`` of a list endpoint (``Page``), which also reports the number
    of items in the whole list in the ``X-Total-Count`` header (``PAGED``)."""

    limit: int
    offset: int
    response: Response

    def apply[Q: GenerativeSelect](self, session: Session, query: Q) -> Q:
        """``query`` limited to this page; counts all its rows for the header."""
        rows = query.order_by(None).subquery()
        total = session.exec(select(func.count()).select_from(rows)).one()
        self.response.headers[TOTAL_COUNT] = str(total)
        return query.limit(self.limit).offset(self.offset)


def _paging(page: Annotated[Page, Query()], response: Response) -> Paging:
    return Paging(page.limit, page.offset, response)


PageDep = Annotated[Paging, Depends(_paging)]


def get_or_404[T: SQLModel](session: Session, model: type[T], id: uuid.UUID) -> T:
    if (row := session.get(model, id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__tablename__} {id} not found")
    return row


def get_or_422[T: SQLModel](
    session: Session, model: type[T], id: uuid.UUID, loc: Sequence[str | int]
) -> T:
    """Fetch a row referenced from the request body; 422 at ``loc`` if it does not exist."""
    if (row := session.get(model, id)) is None:
        raise unprocessable(loc, f"{model.__tablename__} {id} not found", str(id))
    return row


def artist_credit_from_input(
    session: Session,
    names: Sequence[ArtistCreditNameIn],
    loc: Sequence[str | int] = ("artist_credit",),
) -> ArtistCredit:
    """The credit for these names (shared if it exists); new artists are created."""
    parts = []
    for i, name in enumerate(names):
        if name.artist_id is not None:
            artist = get_or_422(session, Artist, name.artist_id, (*loc, i, "artist_id"))
        else:
            assert name.new_artist is not None
            new = name.new_artist
            artist = Artist(
                name=new.name,
                sort_name=new.sort_name or new.name,
                disambiguation=new.disambiguation,
                type=new.type,
            )
            session.add(artist)
        parts.append(CreditPart(artist, name.credited_name or artist.name, name.join_phrase))
    return get_or_create_artist_credit(session, parts)
