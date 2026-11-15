"""The job queue: Procrastinate on EP's Postgres database (schema: migration 0006).

The queue carries one kind of task, "run import job <id>" (``ep.jobs.tasks``); the
import job's state, log and proposal live in ``import_job``.

Two Procrastinate apps share the database:

- ``app`` (async psycopg pool) runs the worker (``ep.jobs.worker``);
- ``defer_app`` (sync, never opened) defers jobs on the caller's own connection, inside
  the SQLAlchemy transaction that creates or re-queues the ``import_job`` row: the row
  and its queue entry are committed together or not at all.
"""

import uuid

from procrastinate import App, PsycopgConnector, SyncPsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import make_url
from sqlmodel import Session

from ep.models import ImportJob, ImportKind
from ep.settings import get_settings

IMPORT_QUEUE = "imports"
RUN_IMPORT = "ep.run_import"


def conninfo() -> str:
    """The libpq connection string for EP's database (from the SQLAlchemy URL)."""
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


app = App(
    connector=PsycopgConnector(conninfo=conninfo(), min_size=1, max_size=4),
    import_paths=["ep.jobs.tasks"],
)
defer_app = App(connector=SyncPsycopgConnector())


def create_import(session: Session, kind: ImportKind, input: str) -> ImportJob:
    """A new queued import job, in the session's transaction (the caller commits)."""
    job = ImportJob(kind=kind, input=input)
    session.add(job)
    session.flush()
    enqueue_import(session, job.id)
    return job


def enqueue_import(session: Session, job_id: uuid.UUID) -> bool:
    """Queue "run import job ``job_id``" in the session's transaction (committed with
    it). False if it is queued already (a queueing lock per job: one waiting entry)."""
    connection = session.connection().connection.driver_connection
    deferrer = defer_app.configure_task(
        RUN_IMPORT, queue=IMPORT_QUEUE, queueing_lock=str(job_id), connection=connection
    )
    try:
        # A savepoint: a duplicate (unique violation) must not abort the transaction.
        with session.begin_nested():
            deferrer.defer(job_id=str(job_id))
    except AlreadyEnqueued:
        return False
    return True
