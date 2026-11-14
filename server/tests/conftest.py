"""Test setup: a real Postgres database, migrated with Alembic.

The test database (default: ``ep_test`` on the same server as EP_DATABASE_URL,
override with EP_TEST_DATABASE_URL) is dropped and recreated once per test
session, then migrated to head. Each test runs inside a transaction that is
rolled back afterwards, so tests do not see each other's data.
"""

import base64
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus
from sqlalchemy import Engine, create_engine, make_url, text
from sqlmodel import Session

from ep.db import get_session
from ep.main import app
from ep.settings import Settings, get_settings

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
def library_dir(tmp_path: Path) -> Path:
    """The managed library for a test: an empty temp dir (never ~/Music/ep-Library)."""
    path = tmp_path / "library"
    path.mkdir()
    return path


@pytest.fixture
def client(session: Session, library_dir: Path, audio_dir: Path) -> Iterator[TestClient]:
    def request_session() -> Iterator[Session]:
        # Like a per-request session: a failed request leaves nothing pending behind.
        try:
            yield session
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_session] = request_session
    # Media roots: the library and the generated audio, nothing else in the temp dirs.
    settings = Settings(library_dir=library_dir, media_roots=[library_dir, audio_dir])
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# Audio fixtures, generated with ffmpeg once per test session.

MB_RECORDING_ID = "8f3471b5-7e6a-48da-86a9-c1c07a0f47ae"
MB_RELEASE_ID = "1e6b3e9a-4e0e-4b9d-9c5d-2b2f0b1c4d5e"
MB_RELEASE_GROUP_ID = "7c3218d7-75e0-4e8c-971f-f097b6c308c5"
MB_ARTIST_IDS = ["dd4f0e2d-5c3e-4a8f-a0d4-17b6d7a9c1e1", "a1a2a3a4-b1b2-c1c2-d1d2-e1e2e3e4e5e6"]


def _ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="session")
def audio_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Small generated audio files: a fully tagged FLAC with a cover, an MP3, an M4A, an Opus."""
    directory = tmp_path_factory.mktemp("audio")
    cover = directory / "cover.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=8x8", "-frames:v", "1", cover)
    with_cover = ("-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy")
    with_cover += ("-disposition:v", "attached_pic")

    def sine(seconds: int) -> tuple[str, ...]:
        return ("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}")

    _ffmpeg(
        *sine(6),
        *with_cover,
        *("-metadata", "title=Sinnerman", "-metadata", "artist=Nina Simone"),
        *("-metadata", "ALBUMARTIST=Nina Simone", "-metadata", "album=Pastel Blues"),
        *("-metadata", "TRACKNUMBER=3/12", "-metadata", "DISCNUMBER=1"),
        *("-metadata", "DISCTOTAL=2", "-metadata", "date=1965-10"),
        *("-metadata", "ISRC=USPR36500123"),
        *("-metadata", f"MUSICBRAINZ_TRACKID={MB_RECORDING_ID}"),
        *("-metadata", f"MUSICBRAINZ_ALBUMID={MB_RELEASE_ID}"),
        *("-metadata", f"MUSICBRAINZ_RELEASEGROUPID={MB_RELEASE_GROUP_ID}"),
        *("-metadata", f"MUSICBRAINZ_ARTISTID={'; '.join(MB_ARTIST_IDS)}"),
        directory / "album.flac",
    )
    _ffmpeg(
        *sine(3),
        *with_cover,
        *("-id3v2_version", "4", "-metadata", "title=Feeling Good", "-metadata", "track=2"),
        directory / "song.mp3",
    )
    _ffmpeg(*sine(2), "-c:a", "aac", "-metadata", "title=Lilac Wine", directory / "song.m4a")
    opus = ("-c:a", "libopus", "-metadata", "title=Ne me quitte pas")
    _ffmpeg(*sine(2), *opus, directory / "song.opus")
    # Ogg covers are base64 FLAC picture blocks in a comment (only the fixture is written).
    picture = Picture()
    picture.type, picture.mime, picture.data = 3, "image/png", cover.read_bytes()
    opus_file = OggOpus(directory / "song.opus")
    opus_file["metadata_block_picture"] = [base64.b64encode(picture.write()).decode()]
    opus_file.save()
    (directory / "notes.txt").write_text("not audio")
    return directory
