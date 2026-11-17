"""The worker process (compose service ``worker``): ``python -m ep.jobs.worker``. With
``EP_RUN_WORKER`` set, the API process runs it instead (``ep.main``, for development).

Runs the Procrastinate worker for the import queue with concurrency 1 (one import at a
time: MusicBrainz is rate limited per process, and imports are I/O on the same disks)
and, in the same event loop, the watcher (``ep.jobs.watcher``). On start, jobs a
previous worker left running are queued again, and the work directories of finished
or dismissed jobs it did not get to remove are removed.
"""

import asyncio
import logging

from sqlmodel import Session

from ep.db import get_engine
from ep.importers.framework import discard_stale_work
from ep.jobs.queue import IMPORT_QUEUE, app
from ep.jobs.tasks import recover_interrupted_jobs
from ep.jobs.watcher import Watcher
from ep.settings import get_settings

logger = logging.getLogger("ep.worker")


def _recover() -> None:
    with Session(get_engine()) as session:
        if recovered := recover_interrupted_jobs(session):
            logger.info("queued %d interrupted import job(s) again", len(recovered))
        if removed := discard_stale_work(session, get_settings().library_dir):
            logger.info("removed the work directories of %d finished job(s)", len(removed))


async def run(*, install_signal_handlers: bool = True) -> None:
    """Recover, then run the import worker and the watcher until stopped: by SIGINT/
    SIGTERM, or (``install_signal_handlers=False``, inside the API) by cancelling."""
    await asyncio.to_thread(_recover)
    async with app.open_async():
        watcher = asyncio.create_task(Watcher(get_settings()).run(get_engine()))
        try:
            # Stops on SIGINT/SIGTERM or cancellation, letting the running job finish.
            await app.run_worker_async(
                queues=[IMPORT_QUEUE],
                concurrency=1,
                delete_jobs="successful",
                install_signal_handlers=install_signal_handlers,
            )
        finally:
            watcher.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())
