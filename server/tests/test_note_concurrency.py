"""Two clients saving the first note of an entity at the same instant: one save wins,
the other is a clean 409 with the winner's note. Needs real, committed transactions
on separate connections (not the per-test rolled-back transaction)."""

import threading
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, col, delete, select

from ep.api import notes as notes_api
from ep.db import get_session
from ep.main import app
from ep.models import Artist, Note


@pytest.fixture
def artist(engine: Engine) -> Iterator[Artist]:
    with Session(engine) as session:
        artist = Artist(name="Nina Simone", sort_name="Simone, Nina")
        session.add(artist)
        session.commit()
        session.refresh(artist)
    yield artist
    with Session(engine) as session:
        session.exec(delete(Note).where(col(Note.entity_id) == artist.id))
        session.exec(delete(Artist).where(col(Artist.id) == artist.id))
        session.commit()


def test_concurrent_first_saves(engine: Engine, artist: Artist) -> None:
    def request_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    # Both requests look for the note (and find none) before either inserts it.
    barrier = threading.Barrier(2, timeout=10)
    lookup = notes_api.store.get_note

    waited = threading.local()

    def get_note_then_wait(*args: Any, **kwargs: Any) -> Note | None:
        note = lookup(*args, **kwargs)
        if not getattr(waited, "done", False):
            waited.done = True
            barrier.wait()
        return note

    responses: dict[str, Any] = {}

    def save(body: str) -> None:
        with TestClient(app) as client:
            responses[body] = client.put(
                f"/notes/artist/{artist.id}", json={"body": body, "base_updated_at": None}
            )

    app.dependency_overrides[get_session] = request_session
    try:
        with mock.patch.object(notes_api.store, "get_note", side_effect=get_note_then_wait):
            threads = [threading.Thread(target=save, args=(body,)) for body in ("one", "two")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
    finally:
        app.dependency_overrides.clear()

    codes = sorted(response.status_code for response in responses.values())
    assert codes == [200, 409], {body: r.text for body, r in responses.items()}
    [winner] = [body for body, r in responses.items() if r.status_code == 200]
    [loser] = [r for r in responses.values() if r.status_code == 409]
    assert loser.json()["current"]["body"] == winner
    assert loser.json()["current"]["updated_at"] is not None
    with Session(engine) as session:
        [note] = session.exec(select(Note).where(Note.entity_id == artist.id)).all()
        assert note.body == winner
