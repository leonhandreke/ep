"""Shared helpers for routers."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from sqlmodel import Session, SQLModel

from ep.api.errors import unprocessable
from ep.api.schemas import ArtistCreditNameIn, Page
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.db import get_session
from ep.models import Artist, ArtistCredit
from ep.settings import Settings, get_settings

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PageDep = Annotated[Page, Query()]


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


def artist_credit_from_input(session: Session, names: Sequence[ArtistCreditNameIn]) -> ArtistCredit:
    parts = []
    for i, name in enumerate(names):
        artist = get_or_422(session, Artist, name.artist_id, ("artist_credit", i, "artist_id"))
        parts.append(CreditPart(artist, name.credited_name or artist.name, name.join_phrase))
    return get_or_create_artist_credit(session, parts)
