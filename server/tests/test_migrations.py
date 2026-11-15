from sqlalchemy import text
from sqlmodel import Session


def test_extensions_enabled(session: Session) -> None:
    names = set(session.connection().execute(text("SELECT extname FROM pg_extension")).scalars())
    assert "pg_trgm" in names


def test_job_queue_schema(session: Session) -> None:
    tables = set(
        session.connection()
        .execute(text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'procrastinate_%'"))
        .scalars()
    )
    assert tables == {
        "procrastinate_events",
        "procrastinate_jobs",
        "procrastinate_periodic_defers",
        "procrastinate_workers",
    }
