"""List endpoints load related rows in a fixed number of queries, however long the list.

Each case builds a small and a larger library and counts the SELECTs of one request,
with an empty session (as a request's own session would be).
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from tests.helpers import credit, make_artist, make_release_group, make_track, post


@contextmanager
def count_selects(session: Session) -> Generator[list[str]]:
    statements: list[str] = []

    def capture(conn: Any, cursor: Any, statement: str, parameters: Any, *args: Any) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    session.expunge_all()
    connection = session.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def build_library(client: TestClient, audio_file: Path, size: int) -> dict[str, str]:
    """An artist with an album of ``size`` tracks and ``size`` singles, each with its own
    artist credit (a duo, joined differently). Each recording is playable as a slice of a
    file downloaded from a link (two levels of parents) and has a second slice; the last
    recording is also on every single."""
    nina, other = make_artist(client), make_artist(client, "Other")

    def duo(position: int) -> list[dict[str, Any]]:
        return credit(nina, other, join=f" & {position} ")

    album = make_release_group(client, nina, artist_credit=duo(0))
    link = post(client, "/sources", {"kind": "youtube", "uri": f"https://youtu.be/{size}"})
    file = post(
        client, "/sources", {"kind": "file", "uri": str(audio_file), "parent_id": link["id"]}
    )
    singles, recording = [], {}
    for position in range(1, size + 1):
        recording = post(
            client, "/recordings", {"title": f"Song {position}", "artist_credit": duo(position)}
        )
        make_track(client, album, recording, position)
        for _ in range(2):
            post(
                client,
                "/sources",
                {
                    "kind": "slice",
                    "parent_id": file["id"],
                    "start_ms": position * 100,
                    "recording_id": recording["id"],
                },
            )
        singles.append(
            make_release_group(
                client, nina, f"Single {position}", artist_credit=duo(position), type="single"
            )
        )
    for single in singles:
        make_track(client, single, recording)
    return {"artist": nina["id"], "release_group": album["id"], "recording": recording["id"]}


CASES: list[tuple[str, Callable[[dict[str, str]], str], int]] = [
    # release group, tracks + recordings + credits, credit names, sources + ancestors
    ("release_group_tracks", lambda ids: f"/release-groups/{ids['release_group']}/tracks", 4),
    # artist, release groups + credits, credit names
    ("artist_release_groups", lambda ids: f"/artists/{ids['artist']}/release-groups", 3),
    ("artist_recordings", lambda ids: f"/artists/{ids['artist']}/recordings", 3),
    # recording + credit + tracks + release groups + their credits, names for each credit
    ("recording_detail", lambda ids: f"/recordings/{ids['recording']}", 3),
    # recording, sources + ancestors
    ("recording_sources", lambda ids: f"/recordings/{ids['recording']}/sources", 2),
    # threshold + normalised query, then per type: by name/title, as "artist title";
    # covers of recording hits
    ("search", lambda ids: "/search?q=nina%20song", 7),
]


@pytest.mark.parametrize(("name", "path", "expected"), CASES, ids=[name for name, *_ in CASES])
def test_list_endpoint_query_count(
    client: TestClient,
    session: Session,
    audio_dir: Path,
    name: str,
    path: Callable[[dict[str, str]], str],
    expected: int,
) -> None:
    counts = []
    for size, audio_file in (1, "album.flac"), (5, "song.mp3"):
        ids = build_library(client, audio_dir / audio_file, size)
        with count_selects(session) as statements:
            response = client.get(path(ids))
        assert response.status_code == 200, response.text
        assert response.json()
        counts.append(len(statements))
    assert counts == [expected, expected], "\n\n".join(statements)
