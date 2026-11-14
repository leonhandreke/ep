"""Test setup: a real Postgres database, migrated with Alembic.

The test database (default: ``ep_test`` on the same server as EP_DATABASE_URL,
override with EP_TEST_DATABASE_URL) is dropped and recreated once per test
session, then migrated to head. Each test runs inside a transaction that is
rolled back afterwards, so tests do not see each other's data.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlmodel import Session

from ep.db import get_session
from ep.main import app
from ep.settings import get_settings

SERVER_DIR = Path(__file__).resolve().parent.parent


def _test_database_url() -> str:
    if url := os.environ.get("EP_TEST_DATABASE_URL"):
        return url
    return (
        make_url(get_settings().database_url)
        .set(database="ep_test")
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = make_url(_test_database_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    config = Config(toml_file=SERVER_DIR / "pyproject.toml")
    config.attributes["database_url"] = url.render_as_string(hide_password=False)
    command.upgrade(config, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
