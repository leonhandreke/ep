from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from ep.db import get_session

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    database: str


@router.get("/health")
def get_health(session: Annotated[Session, Depends(get_session)]) -> Health:
    try:
        session.connection().execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return Health(status="ok", database="ok")
