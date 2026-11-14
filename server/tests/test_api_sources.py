import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from ep.models import Source, SourceKind
from ep.playback import NotPlayable, resolve
from tests.helpers import make_artist, make_recording, make_release_group, make_track, post


def make_source(client: TestClient, kind: str, **fields: Any) -> dict[str, Any]:
    return post(client, "/sources", {"kind": kind, **fields})


def test_create_file_source_reads_facts_from_the_file(client: TestClient, audio_dir: Path) -> None:
    path = audio_dir / "album.flac"
    source = make_source(client, "file", uri=str(path))
    assert source["codec"] == "flac"
    assert 5900 < source["duration_ms"] < 6100
    assert source["size"] == path.stat().st_size
    assert source["managed"] is False
    assert source["meta"] == {}
    assert source["playback"] == {
        "source_id": source["id"],
        "file_source_id": source["id"],
        "start_ms": 0,
        "end_ms": None,
    }
    assert client.get(f"/sources/{source['id']}").json() == source

    # Given facts win over probed ones.
    other = make_source(client, "file", uri=str(audio_dir / "song.mp3"), codec="x", size=1)
    assert (other["codec"], other["size"]) == ("x", 1)


def test_create_source_validation(client: TestClient, audio_dir: Path) -> None:
    file = make_source(client, "file", uri=str(audio_dir / "album.flac"))

    def create(**body: Any) -> tuple[int, list[Any] | None]:
        response = client.post("/sources", json=body)
        detail = response.json().get("detail")
        loc = detail[0]["loc"] if isinstance(detail, list) else None
        return response.status_code, loc

    assert create(kind="file", uri="relative/path.flac") == (422, ["body", "uri"])
    assert create(kind="file", uri=str(audio_dir / "missing.flac")) == (422, ["body", "uri"])
    assert create(kind="file", uri=str(audio_dir / "notes.txt")) == (422, ["body", "uri"])
    assert create(kind="file", uri=str(audio_dir / "album.flac"))[0] == 409  # indexed already
    assert create(kind="youtube") == (422, ["body", "uri"])
    assert create(kind="youtube", uri="https://y.example/v", start_ms=0) == (
        422,
        ["body", "start_ms"],
    )
    assert create(kind="slice", start_ms=0) == (422, ["body", "parent_id"])
    assert create(kind="slice", parent_id=file["id"]) == (422, ["body", "start_ms"])
    assert create(kind="slice", parent_id=file["id"], start_ms=10, end_ms=10) == (
        422,
        ["body", "end_ms"],
    )
    assert create(kind="slice", parent_id=file["id"], start_ms=0, uri="x") == (422, ["body", "uri"])
    assert create(kind="slice", parent_id=str(uuid.uuid4()), start_ms=0) == (
        422,
        ["body", "parent_id"],
    )
    assert create(kind="youtube", uri="https://y", recording_id=str(uuid.uuid4())) == (
        422,
        ["body", "recording_id"],
    )
    assert create(kind="mixcloud", uri="https://m")[0] == 422
    assert create(kind="youtube", uri="https://y", sha256="ABC")[0] == 422


def test_remote_link_is_stored_but_not_playable(client: TestClient) -> None:
    nina = make_artist(client)
    recording = make_recording(client, nina)
    link = make_source(
        client,
        "spotify",
        uri="spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        recording_id=recording["id"],
        meta={"popularity": 3},
    )
    assert link["playback"] is None
    assert link["meta"] == {"popularity": 3}
    response = client.get(f"/sources/{link['id']}/stream")
    assert response.status_code == 409
    assert "no file to play" in response.json()["detail"]


def test_slices_resolve_to_the_file_with_absolute_offsets(
    client: TestClient, audio_dir: Path
) -> None:
    link = make_source(client, "youtube", uri="https://www.youtube.com/watch?v=x")
    file = make_source(client, "file", uri=str(audio_dir / "album.flac"), parent_id=link["id"])
    side = make_source(client, "slice", parent_id=file["id"], start_ms=1000, end_ms=5000)
    tail = make_source(client, "slice", parent_id=file["id"], start_ms=5000)
    nested = make_source(client, "slice", parent_id=side["id"], start_ms=500, end_ms=1500)
    overlong = make_source(client, "slice", parent_id=side["id"], start_ms=3000, end_ms=9000)
    open_ended = make_source(client, "slice", parent_id=side["id"], start_ms=2000)

    def playback(source: dict[str, Any]) -> tuple[str, int, int | None]:
        p = client.get(f"/sources/{source['id']}").json()["playback"]
        assert p["source_id"] == source["id"]
        return p["file_source_id"], p["start_ms"], p["end_ms"]

    assert playback(side) == (file["id"], 1000, 5000)
    assert playback(tail) == (file["id"], 5000, None)
    assert playback(nested) == (file["id"], 1500, 2500)
    assert playback(overlong) == (file["id"], 4000, 5000)  # clipped to its parent
    assert playback(open_ended) == (file["id"], 3000, 5000)
    assert client.get(f"/sources/{link['id']}").json()["playback"] is None

    # A slice beyond the end of its parent slice is not playable.
    outside = make_source(client, "slice", parent_id=side["id"], start_ms=4000)
    assert client.get(f"/sources/{outside['id']}").json()["playback"] is None


def test_resolve_guards_against_cycles(session: Session) -> None:
    a = Source(kind=SourceKind.slice, start_ms=0)
    b = Source(kind=SourceKind.slice, start_ms=0)
    session.add_all([a, b])
    session.flush()
    # The API refuses this; the database alone does not.
    a.parent_id, b.parent_id = b.id, a.id
    session.flush()
    session.expire_all()
    with pytest.raises(NotPlayable, match="cycle"):
        resolve(a)


def test_update_source(client: TestClient, audio_dir: Path) -> None:
    nina = make_artist(client)
    recording = make_recording(client, nina)
    file = make_source(client, "file", uri=str(audio_dir / "album.flac"))
    piece = make_source(client, "slice", parent_id=file["id"], start_ms=0, end_ms=1000)
    child = make_source(client, "slice", parent_id=piece["id"], start_ms=0)
    url = f"/sources/{piece['id']}"

    response = client.patch(
        url, json={"recording_id": recording["id"], "end_ms": None, "meta": {"a": 1}}
    )
    assert response.status_code == 200
    updated = response.json()
    assert (updated["recording_id"], updated["end_ms"], updated["meta"]) == (
        recording["id"],
        None,
        {"a": 1},
    )
    assert updated["start_ms"] == 0

    assert client.patch(url, json={"parent_id": piece["id"]}).status_code == 422
    assert client.patch(url, json={"parent_id": child["id"]}).status_code == 422  # a cycle
    assert client.patch(url, json={"start_ms": None}).status_code == 422
    assert client.patch(url, json={"kind": "file"}).status_code == 422  # a file needs a uri
    assert client.patch(url, json={"managed": None}).status_code == 422
    assert client.get(url).json() == updated
    assert client.patch(f"/sources/{uuid.uuid4()}", json={}).status_code == 404


def test_delete_source_keeps_the_file(client: TestClient, audio_dir: Path) -> None:
    path = audio_dir / "song.mp3"
    file = make_source(client, "file", uri=str(path))
    piece = make_source(client, "slice", parent_id=file["id"], start_ms=0)
    assert client.delete(f"/sources/{file['id']}").status_code == 409  # has a slice
    assert client.delete(f"/sources/{piece['id']}").status_code == 204
    assert client.delete(f"/sources/{file['id']}").status_code == 204
    assert client.get(f"/sources/{file['id']}").status_code == 404
    assert client.delete(f"/sources/{file['id']}").status_code == 404
    assert path.is_file()


def test_recording_sources_and_track_playback(client: TestClient, audio_dir: Path) -> None:
    nina = make_artist(client)
    release_group = make_release_group(client, nina)
    first = make_recording(client, nina, "First")
    second = make_recording(client, nina, "Second")
    make_track(client, release_group, first, 1)
    make_track(client, release_group, second, 2)

    album = make_source(client, "file", uri=str(audio_dir / "album.flac"))
    link = make_source(client, "bandcamp", uri="https://b.example/t", recording_id=first["id"])
    piece = make_source(
        client, "slice", parent_id=album["id"], start_ms=2000, end_ms=4000, recording_id=first["id"]
    )

    sources = client.get(f"/recordings/{first['id']}/sources").json()
    assert [s["id"] for s in sources] == [link["id"], piece["id"]]
    assert client.get(f"/recordings/{second['id']}/sources").json() == []
    assert client.get(f"/recordings/{uuid.uuid4()}/sources").status_code == 404

    tracks = client.get(f"/release-groups/{release_group['id']}/tracks").json()
    assert [t["playback"] for t in tracks] == [
        {"source_id": piece["id"], "file_source_id": album["id"], "start_ms": 2000, "end_ms": 4000},
        None,
    ]
    assert client.get(f"/tracks/{tracks[0]['id']}").json()["playback"] == tracks[0]["playback"]


def test_delete_recording_with_sources_is_a_conflict(client: TestClient) -> None:
    recording = make_recording(client, make_artist(client))
    link = make_source(client, "youtube", uri="https://y.example/v", recording_id=recording["id"])
    response = client.delete(f"/recordings/{recording['id']}")
    assert response.status_code == 409
    assert response.json() == {"detail": "recording still has sources"}
    assert client.delete(f"/sources/{link['id']}").status_code == 204
    assert client.delete(f"/recordings/{recording['id']}").status_code == 204


def test_stream_whole_file_and_ranges(client: TestClient, audio_dir: Path) -> None:
    path = audio_dir / "album.flac"
    data = path.read_bytes()
    file = make_source(client, "file", uri=str(path))
    url = f"/sources/{file['id']}/stream"

    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/flac"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == data

    response = client.get(url, headers={"Range": "bytes=0-1"})  # AVPlayer's first probe
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-1/{len(data)}"
    assert response.headers["content-length"] == "2"
    assert response.content == data[:2]

    response = client.get(url, headers={"Range": "bytes=100-"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-{len(data) - 1}/{len(data)}"
    assert response.content == data[100:]

    response = client.get(url, headers={"Range": "bytes=-10"})
    assert response.status_code == 206
    assert response.content == data[-10:]

    response = client.get(url, headers={"Range": f"bytes={len(data)}-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(data)}"


def test_stream_slice_serves_the_whole_underlying_file(client: TestClient, audio_dir: Path) -> None:
    path = audio_dir / "song.m4a"
    file = make_source(client, "file", uri=str(path))
    piece = make_source(client, "slice", parent_id=file["id"], start_ms=500, end_ms=1500)
    response = client.get(f"/sources/{piece['id']}/stream", headers={"Range": "bytes=0-"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "audio/mp4"
    assert response.content == path.read_bytes()


def test_stream_missing_file_is_not_found(
    client: TestClient, tmp_path: Path, audio_dir: Path
) -> None:
    path = tmp_path / "gone.mp3"
    path.write_bytes((audio_dir / "song.mp3").read_bytes())
    file = make_source(client, "file", uri=str(path))
    path.unlink()
    response = client.get(f"/sources/{file['id']}/stream")
    assert response.status_code == 404
    assert "file not found" in response.json()["detail"]
    assert client.get(f"/sources/{uuid.uuid4()}/stream").status_code == 404
