import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.exc import DataError, IntegrityError

from ep.api import router
from ep.api.errors import data_error_handler, integrity_error_handler
from ep.settings import get_settings


def operation_id(route: APIRoute) -> str:
    # The endpoint function name, e.g. "list_artists": readable names for the
    # generated Swift client.
    return route.name


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """With ``EP_RUN_WORKER``, the import worker and the watcher run in this process
    (``ep.jobs.worker``); shutting down waits for a running import to finish."""
    if not get_settings().run_worker:
        yield
        return
    from ep.jobs import worker  # the queue's connection pool only when it is used

    # Uvicorn configures only its own loggers; the worker's and the watcher's log too.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
    task = asyncio.create_task(worker.run(install_signal_handlers=False))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="EP", generate_unique_id_function=operation_id, lifespan=lifespan)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(DataError, data_error_handler)
app.include_router(router)
