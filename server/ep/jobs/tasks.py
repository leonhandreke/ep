"""Procrastinate tasks, run by the worker (``ep.jobs.worker``).

MusicBrainz is called from here only (and from the API's own MusicBrainz endpoints):
the worker runs one job at a time, and the process-wide rate limiter
(``ep.metadata.musicbrainz.LIMITER``) keeps it at one request per second.
"""

import uuid

from sqlmodel import Session, col, select

from ep.db import get_engine
from ep.importers.bandcamp import BandcampImporter
from ep.importers.folder import FolderImporter
from ep.importers.framework import Importer, requeue, run_job
from ep.importers.youtube import YouTubeImporter
from ep.jobs.queue import IMPORT_QUEUE, RUN_IMPORT, app, enqueue_import
from ep.metadata.musicbrainz import MusicBrainz
from ep.models import ImportJob, ImportKind, JobState
from ep.settings import get_settings

IMPORTERS: dict[ImportKind, Importer] = {
    ImportKind.folder: FolderImporter(),
    ImportKind.bandcamp: BandcampImporter(),
    ImportKind.youtube: YouTubeImporter(),
}


@app.task(name=RUN_IMPORT, queue=IMPORT_QUEUE)
def run_import(job_id: str) -> None:
    with Session(get_engine()) as session, MusicBrainz() as musicbrainz:
        run_job(session, uuid.UUID(job_id), IMPORTERS, musicbrainz, get_settings())


def recover_interrupted_jobs(session: Session) -> list[uuid.UUID]:
    """Queue again the jobs a stopped worker left ``running`` or ``applying`` (an
    interrupted apply was rolled back, so running again is safe), and make sure every
    ``queued`` job has its queue entry (one lost to a failed task would wait forever;
    the queueing lock keeps this from adding a second one). For worker start-up, when
    no job of this (single) worker can be running. Returns the jobs re-queued."""
    states = [JobState.running, JobState.applying, JobState.queued]
    jobs = session.exec(select(ImportJob).where(col(ImportJob.state).in_(states))).all()
    recovered = []
    for job in jobs:
        interrupted = job.state is not JobState.queued
        if interrupted:
            requeue(job)
        if enqueue_import(session, job.id) or interrupted:
            recovered.append(job.id)
    session.commit()
    return recovered
