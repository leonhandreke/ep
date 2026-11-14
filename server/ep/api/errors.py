"""Error responses: 404/409 bodies, constraint violations, request-level 422s."""

from collections.abc import Sequence
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.errors import IntegrityError as PsycopgIntegrityError
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError


class ErrorRead(BaseModel):
    """The body of a 404 or 409 response."""

    detail: str


NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorRead, "description": "Not found"}
}
CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": ErrorRead, "description": "Conflicts with existing data"}
}
BAD_GATEWAY: dict[int | str, dict[str, Any]] = {
    status.HTTP_502_BAD_GATEWAY: {"model": ErrorRead, "description": "Upstream service failed"}
}


def integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A constraint violation (duplicate MBID, track position, ...) on flush or commit is 409.

    The request's session is rolled back when the session dependency exits.
    """
    assert isinstance(exc, IntegrityError)
    constraint = None
    if isinstance(exc.orig, PsycopgIntegrityError):
        constraint = exc.orig.diag.constraint_name
    return JSONResponse(
        {"detail": f"conflicts with existing data ({constraint})"},
        status_code=status.HTTP_409_CONFLICT,
    )


def unprocessable(
    loc: Sequence[str | int], msg: str, input: Any, type: str = "not_found"
) -> RequestValidationError:
    """A 422 in the same shape as FastAPI's validation errors (``HTTPValidationError``)."""
    return RequestValidationError(
        [{"type": type, "loc": ("body", *loc), "msg": msg, "input": input}]
    )
