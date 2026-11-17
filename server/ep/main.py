import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.exc import DataError, IntegrityError

from ep.api import router
from ep.api.errors import data_error_handler, integrity_error_handler
from ep.settings import get_settings

logger = logging.getLogger("ep.main")


def operation_id(route: APIRoute) -> str:
    # The endpoint function name, e.g. "list_artists": readable names for the
    # generated Swift client.
    return route.name


def _worker_stopped(task: asyncio.Task[None]) -> None:
    """The in-process worker stopped without being cancelled (an exception, or it
    returned): log why and stop the server, rather than serve with nothing importing."""
    if task.cancelled():
        return
    if exc := task.exception():
        logger.critical("the import worker failed; stopping the server", exc_info=exc)
    else:
        logger.critical("the import worker stopped; stopping the server")
    signal.raise_signal(signal.SIGTERM)  # uvicorn shuts down as on Ctrl-C


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """With ``EP_RUN_WORKER``, the import worker and the watcher run in this process
    (``ep.jobs.worker``); shutting down waits for a running import to finish. If the
    worker stops on its own, the server stops too (``_worker_stopped``)."""
    if not get_settings().run_worker:
        yield
        return
    from ep.jobs import worker  # the queue's connection pool only when it is used

    # Uvicorn configures only its own loggers; the worker's and the watcher's log too.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
    logger.info("running the import worker and the watcher in this process")
    task = asyncio.create_task(worker.run(install_signal_handlers=False), name="ep-worker")
    task.add_done_callback(_worker_stopped)
    try:
        yield
    finally:
        task.remove_done_callback(_worker_stopped)
        task.cancel()
        # A failure was logged by ``_worker_stopped`` already.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(title="EP", generate_unique_id_function=operation_id, lifespan=lifespan)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(DataError, data_error_handler)
app.include_router(router)
