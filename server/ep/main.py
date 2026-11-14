from fastapi import FastAPI
from fastapi.routing import APIRoute

from ep.api import router


def operation_id(route: APIRoute) -> str:
    # The endpoint function name, e.g. "list_artists": readable names for the
    # generated Swift client.
    return route.name


app = FastAPI(title="EP", generate_unique_id_function=operation_id)
app.include_router(router)
