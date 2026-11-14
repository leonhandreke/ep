from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError

from ep.api import router
from ep.api.errors import integrity_error_handler


def operation_id(route: APIRoute) -> str:
    # The endpoint function name, e.g. "list_artists": readable names for the
    # generated Swift client.
    return route.name


app = FastAPI(title="EP", generate_unique_id_function=operation_id)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.include_router(router)
