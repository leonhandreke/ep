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

from ep.jobs.queue import create_import
from ep.models import ImportKind
from ep.settings import Settings
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
    # ... and sources + ancestors (playability)
    ("artist_recordings", lambda ids: f"/artists/{ids['artist']}/recordings", 4),
    # artist, its counts (one statement)
    ("artist_detail", lambda ids: f"/artists/{ids['artist']}", 2),
    # recording + credit + tracks + release groups + their credits, names for each
    # credit, sources + ancestors
    ("recording_detail", lambda ids: f"/recordings/{ids['recording']}", 4),
    # release group + credit + names, tracks + recordings, sources + ancestors
    ("release_group_detail", lambda ids: f"/release-groups/{ids['release_group']}", 4),
    # recording, sources + ancestors (twice: the list, and what plays)
    ("recording_sources", lambda ids: f"/recordings/{ids['recording']}/sources", 3),
    # threshold + normalised query, then per type: by name/title, as "artist title";
    # radio entries that played hits (tracks of release group hits, entries), release
    # groups of recording hits, track counts, artist counts (release groups, loose)
    ("search", lambda ids: "/search?q=nina%20song", 14),
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


def test_import_lists_query_count(client: TestClient, session: Session, settings: Settings) -> None:
    """One statement per list, however many jobs (with proposals) or watch dirs; the
    job list one more for the queue order (queued jobs)."""
    music = settings.media_roots[2]
    counts = []
    for size in (1, 5):
        for n in range(size):
            job = create_import(session, ImportKind.folder, str(music / f"{size}-{n}"))
            job.proposal = {"issues": [], "releases": []}
            path = music / f"watch-{size}-{n}"
            path.mkdir()
            assert client.post("/watch-dirs", json={"path": str(path)}).status_code == 201
        session.commit()
        for list_path in ("/imports", "/watch-dirs"):
            with count_selects(session) as statements:
                response = client.get(list_path)
            assert response.status_code == 200
            counts.append(len(statements))
    assert counts == [2, 1, 2, 1]


def build_notes(client: TestClient, size: int) -> dict[str, str]:
    """A target artist, and ``size`` artists, release groups and recordings (on the
    release groups) whose notes link to it (and to each other, without display text) and
    mention a word; all tagged with one tag."""
    target = make_artist(client, f"Target {size}")
    word, tag = f"word{size}", f"tag{size}"
    tag_id = ""
    for n in range(size):
        artist = make_artist(client, f"Artist {size} {n}")
        album = make_release_group(client, artist, f"Album {size} {n}")
        song = post(client, "/recordings", {"title": f"S {n}", "artist_credit": credit(artist)})
        make_track(client, album, song)
        for type, entity in ("artist", artist), ("release_group", album), ("recording", song):
            body = (
                f"{word}: [[artist:{target['id']}|T]] [[release_group:{album['id']}]]\n"
                f"and [[recording:{song['id']}]] [[artist:{target['id']}]]"
            )
            response = client.put(
                f"/notes/{type}/{entity['id']}", json={"body": body, "base_updated_at": None}
            )
            assert response.status_code == 200, response.text
            response = client.post(f"/entity-tags/{type}/{entity['id']}", json={"name": tag})
            tag_id = response.json()[0]["id"]
        last = song
    return {"target": target["id"], "word": word, "tag": tag_id, "recording": last["id"]}


NOTE_CASES: list[tuple[str, Callable[[dict[str, str]], str], int]] = [
    # target; links + notes; owners per type (3); their covers; other link targets
    # without display text, per type (2)
    ("backlinks", lambda ids: f"/backlinks/artist/{ids['target']}", 8),
    # entity; note; its links; link targets per type (3); covers
    ("note", lambda ids: f"/notes/recording/{ids['recording']}", 7),
    # notes; owners per type (3); covers
    ("note_search", lambda ids: f"/notes/search?q={ids['word']}", 5),
    ("tags", lambda ids: "/tags", 1),
    # tag; tagged rows; entities per type (3); covers
    ("tag_entities", lambda ids: f"/tags/{ids['tag']}/entities", 6),
    # entity; tags with counts
    ("entity_tags", lambda ids: f"/entity-tags/artist/{ids['target']}", 2),
    # entity; note (none: no links); tags; backlinks (as above, less the entity);
    # radio appearances
    ("entity_details", lambda ids: f"/entity-details/artist/{ids['target']}", 11),
]


@pytest.mark.parametrize(
    ("name", "path", "expected"), NOTE_CASES, ids=[name for name, *_ in NOTE_CASES]
)
def test_note_and_tag_lists_query_count(
    client: TestClient,
    session: Session,
    name: str,
    path: Callable[[dict[str, str]], str],
    expected: int,
) -> None:
    counts = []
    for size in 1, 5:
        ids = build_notes(client, size)
        with count_selects(session) as statements:
            response = client.get(path(ids))
        assert response.status_code == 200, response.text
        counts.append(len(statements))
    assert counts == [expected, expected], "\n\n".join(statements)
