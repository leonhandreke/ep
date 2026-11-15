"""procrastinate job queue schema

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15

The schema of the Procrastinate version EP uses (``schema.sql`` from the package,
copied to ``alembic/procrastinate/`` so that this revision stays what it was when
Procrastinate is upgraded). Upgrading Procrastinate: add one revision per new script in
``procrastinate schema --migrations-path`` (pre and post scripts in order; EP stops the
api and worker for upgrades, so both can run in one revision).

Procrastinate's tables are not SQLModel tables; ``alembic/env.py`` leaves them out of
autogenerate.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = Path(__file__).parent.parent / "procrastinate" / "schema-3.11.0.sql"

TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
FUNCTIONS = (
    "procrastinate_cancel_job_v1",
    "procrastinate_defer_jobs_v1",
    "procrastinate_defer_periodic_job_v2",
    "procrastinate_fetch_job_v2",
    "procrastinate_finish_job_v1",
    "procrastinate_notify_queue_abort_job_v1",
    "procrastinate_notify_queue_job_inserted_v1",
    "procrastinate_prune_stalled_workers_v1",
    "procrastinate_register_worker_v1",
    "procrastinate_retry_job_v1",
    "procrastinate_retry_job_v2",
    "procrastinate_trigger_abort_requested_events_procedure_v1",
    "procrastinate_trigger_function_scheduled_events_v1",
    "procrastinate_trigger_function_status_events_insert_v1",
    "procrastinate_trigger_function_status_events_update_v1",
    "procrastinate_unlink_periodic_defers_v1",
    "procrastinate_unregister_worker_v1",
    "procrastinate_update_heartbeat_v1",
)
TRIGGER_FUNCTIONS = tuple(
    f for f in FUNCTIONS if "_trigger_" in f or "_notify_" in f or "_unlink_" in f
)
TYPES = (
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
)


def upgrade() -> None:
    # Several statements with dollar quoting and casts: run as one simple query on the
    # driver connection (no bind parameter parsing), in Alembic's transaction.
    driver_connection = op.get_bind().connection.driver_connection
    assert driver_connection is not None
    driver_connection.execute(SCHEMA.read_text())


def downgrade() -> None:
    # Functions that use the tables' row types, then the tables (with their triggers),
    # then the trigger functions and types.
    for function in FUNCTIONS:
        if function not in TRIGGER_FUNCTIONS:
            op.execute(f"DROP FUNCTION {function}")
    op.execute(f"DROP TABLE {', '.join(TABLES)}")
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    op.execute(f"DROP TYPE {', '.join(TYPES)}")
