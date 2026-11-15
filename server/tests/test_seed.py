"""The demo seed: fills an empty scratch database and directory, refuses anything else."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlmodel import Session

from ep.db import get_session
from ep.main import app
from ep.seed import SeedError, seed
from ep.settings import Settings, get_settings


@pytest.fixture
def scratch_url(engine: Engine) -> Iterator[str]:
    url = engine.url.set(database="ep_test_seed")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "ep_test_seed" WITH (FORCE)'))
        conn.execute(text('CREATE DATABASE "ep_test_seed"'))
    yield url.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "ep_test_seed" WITH (FORCE)'))
    admin.dispose()


def test_seed_fills_every_state(scratch_url: str, tmp_path: Path) -> None:
    library, watch = tmp_path / "library", tmp_path / "watch"
    seed(scratch_url, library, watch)
    with pytest.raises(SeedError, match="not empty"):
        seed(scratch_url, tmp_path / "other" / "library", tmp_path / "other" / "watch")
    with pytest.raises(SeedError, match="~/Music"):
        seed(scratch_url, Path.home() / "Music" / "ep-seed-test", tmp_path / "w")
    assert not (Path.home() / "Music" / "ep-seed-test").exists()

    engine = create_engine(make_url(scratch_url))
    try:
        with Session(engine) as session:
            app.dependency_overrides[get_session] = lambda: session
            settings = Settings(library_dir=library, media_roots=[library, watch])
            app.dependency_overrides[get_settings] = lambda: settings
            with TestClient(app) as client:
                check(client)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def check(client: TestClient) -> None:
    artists = client.get("/artists", params={"include_radio_only": True}).json()
    names = {artist["name"]: artist for artist in artists}
    assert {"Björk", "Sigur Rós", "Laurel Halo"} <= set(names)
    bjork = names["Björk"]["id"]
    release_groups = client.get(f"/artists/{bjork}/release-groups").json()
    states = set()
    for release_group in release_groups:
        tracks = client.get(f"/release-groups/{release_group['id']}/tracks").json()
        states |= {track["playability"]["state"] for track in tracks}
        assert release_group["cover_url"]
    loose = client.get(f"/artists/{bjork}/recordings", params={"standalone": True}).json()
    states |= {recording["playability"]["state"] for recording in loose}
    assert states == {"file", "slice", "radio_excerpt", "file_missing", "link_only", "no_source"}
    [show] = client.get("/radio/shows").json()
    episodes = client.get(f"/radio/shows/{show['id']}/episodes").json()
    entries = [
        entry
        for episode in episodes
        for entry in client.get(f"/radio/episodes/{episode['id']}").json()["tracks"]
    ]
    assert {entry["offset_is_estimate"] for entry in entries} == {True, False}
    assert {entry["state"] for entry in entries} == {"in_library", "excerpt_only", "not_matched"}
    jobs = client.get("/imports").json()
    assert {job["state"] for job in jobs} == {
        "queued",
        "running",
        "needs_review",
        "failed",
        "done",
    }
    details = client.get(f"/entity-details/artist/{bjork}").json()
    assert any(link["target"] is None for link in details["note"]["links"])  # a broken link
    assert details["tags"]
    summary = client.get("/server").json()
    assert summary["counts"]["radio_only_artists"] == 2
