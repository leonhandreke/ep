"""Shared helpers for routers."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from psycopg.errors import IntegrityError as PsycopgIntegrityError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from ep.api.schemas import ArtistCreditNameIn, Page
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.db import get_session
from ep.models import Artist, ArtistCredit

SessionDep = Annotated[Session, Depends(get_session)]
PageDep = Annotated[Page, Query()]


def get_or_404[T: SQLModel](session: Session, model: type[T], id: uuid.UUID) -> T:
    if (row := session.get(model, id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__tablename__} {id} not found")
    return row


def commit(session: Session) -> None:
    """Commit, turning a constraint violation (duplicate MBID, track position, ...) into 409."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = None
        if isinstance(exc.orig, PsycopgIntegrityError):
            constraint = exc.orig.diag.constraint_name
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"conflicts with existing data ({constraint})"
        ) from exc


def artist_credit_from_input(session: Session, names: Sequence[ArtistCreditNameIn]) -> ArtistCredit:
    parts = []
    for name in names:
        artist = session.get(Artist, name.artist_id)
        if artist is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"artist {name.artist_id} not found"
            )
        parts.append(CreditPart(artist, name.credited_name or artist.name, name.join_phrase))
    return get_or_create_artist_credit(session, parts)
