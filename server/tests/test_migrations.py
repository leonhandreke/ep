from sqlalchemy import text
from sqlmodel import Session


def test_extensions_enabled(session: Session) -> None:
    names = set(session.connection().execute(text("SELECT extname FROM pg_extension")).scalars())
    assert "pg_trgm" in names
