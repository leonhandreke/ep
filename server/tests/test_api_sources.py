import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col

from ep.media_roots import is_within_media_roots
from ep.models import Source, SourceKind
from ep.playback import NotPlayable, resolve, resolve_where
from ep.settings import Settings
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
    assert (source["sample_rate"], source["bit_depth"], source["channels"]) == (44100, 16, 1)
    assert source["bit_rate"] > 0
    assert source["host_path"] == str(path)  # no EP_HOST_PATHS: the same
    assert "meta" not in source
    assert source["playback"] == {
        "source_id": source["id"],
        "file_source_id": source["id"],
        "start_ms": 0,
        "end_ms": None,
        "file_duration_ms": source["duration_ms"],
        "media_type": "audio/flac",
        "codec": "flac",
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
    )
    assert link["playback"] is None
    assert link["host_path"] is None
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
    # The recursive query that loads ancestors for lists stops at the cycle too.
    assert resolve_where(session, col(Source.id) == a.id) == [(a, None)]


def test_update_source(client: TestClient, audio_dir: Path) -> None:
    nina = make_artist(client)
    recording = make_recording(client, nina)
    file = make_source(client, "file", uri=str(audio_dir / "album.flac"))
    piece = make_source(client, "slice", parent_id=file["id"], start_ms=0, end_ms=1000)
    child = make_source(client, "slice", parent_id=piece["id"], start_ms=0)
    url = f"/sources/{piece['id']}"

    response = client.patch(url, json={"recording_id": recording["id"], "end_ms": None})
    assert response.status_code == 200
    updated = response.json()
    assert (updated["recording_id"], updated["end_ms"]) == (recording["id"], None)
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
    assert [s["source"]["id"] for s in sources] == [link["id"], piece["id"]]
    assert [s["role"] for s in sources] == ["link", "slice"]
    assert [s["plays"] for s in sources] == [False, True]
    assert sources[0]["file"] is None
    assert sources[1]["file"]["source_id"] == album["id"]
    assert sources[1]["file"]["exists"] is True
    assert sources[1]["ancestors"][0]["id"] == album["id"]
    assert client.get(f"/recordings/{second['id']}/sources").json() == []
    assert client.get(f"/recordings/{uuid.uuid4()}/sources").status_code == 404

    tracks = client.get(f"/release-groups/{release_group['id']}/tracks").json()
    duration = album["duration_ms"]
    assert [t["playability"]["playback"] for t in tracks] == [
        {
            "source_id": piece["id"],
            "file_source_id": album["id"],
            "start_ms": 2000,
            "end_ms": 4000,
            "file_duration_ms": duration,
            "media_type": "audio/flac",
            "codec": "flac",
        },
        None,
    ]
    assert [t["playability"]["state"] for t in tracks] == ["slice", "no_source"]
    assert tracks[0]["playability"]["link"] == {
        "source_id": link["id"],
        "kind": "bandcamp",
        "url": "https://b.example/t",
    }
    track = client.get(f"/tracks/{tracks[0]['id']}").json()
    assert track["playability"] == tracks[0]["playability"]


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
    client: TestClient, library_dir: Path, audio_dir: Path
) -> None:
    path = library_dir / "gone.mp3"
    path.write_bytes((audio_dir / "song.mp3").read_bytes())
    file = make_source(client, "file", uri=str(path))
    path.unlink()
    response = client.get(f"/sources/{file['id']}/stream")
    assert response.status_code == 404
    assert "file not found" in response.json()["detail"]
    assert client.get(f"/sources/{uuid.uuid4()}/stream").status_code == 404


def test_file_sources_must_be_under_the_media_roots(
    client: TestClient, session: Session, tmp_path: Path, library_dir: Path, audio_dir: Path
) -> None:
    song = audio_dir / "song.mp3"
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(song.read_bytes())
    escape = library_dir / "escape.mp3"
    escape.symlink_to(outside)
    (library_dir / "linked").symlink_to(audio_dir, target_is_directory=True)

    def create(uri: str | Path) -> int:
        return client.post("/sources", json={"kind": "file", "uri": str(uri)}).status_code

    for uri in (outside, escape, library_dir / ".." / "outside.mp3", "/etc/hosts"):
        response = client.post("/sources", json={"kind": "file", "uri": str(uri)})
        assert response.status_code == 422, uri
        assert response.json()["detail"][0]["loc"] == ["body", "uri"]
        assert "media roots" in response.json()["detail"][0]["msg"]
    assert create(library_dir / "linked" / "song.mp3") == 201  # a symlink into a root is fine

    file = make_source(client, "file", uri=str(song))
    assert client.patch(f"/sources/{file['id']}", json={"uri": str(outside)}).status_code == 422
    link = make_source(client, "youtube", uri="https://y.example/v")
    response = client.patch(f"/sources/{link['id']}", json={"kind": "file", "uri": str(escape)})
    assert response.status_code == 422

    # Rows that bypassed the API (or roots that changed since) are refused at stream time.
    for path in (outside, escape):
        row = Source(kind=SourceKind.file, uri=str(path))
        session.add(row)
        session.flush()
        piece = make_source(client, "slice", parent_id=str(row.id), start_ms=0)
        for source_id in (row.id, piece["id"]):
            response = client.get(f"/sources/{source_id}/stream")
            assert response.status_code == 403, path
            assert response.json() == {"detail": "file is outside the media roots"}


def test_default_media_roots_are_the_library_and_watch_mounts(tmp_path: Path) -> None:
    roots = Settings(library_dir=tmp_path).media_roots
    assert roots == [tmp_path, Path("/watch")]
    assert is_within_media_roots(Path("/watch/Library/a/b.flac"), roots)
    assert is_within_media_roots(tmp_path / "x.flac", roots)
    assert not is_within_media_roots(Path("/watchlist/b.flac"), roots)
    assert not is_within_media_roots(Path("/watch/../etc/passwd"), roots)


def test_unique_kind_and_uri(client: TestClient, audio_dir: Path) -> None:
    """A path is indexed once and a link stored once; null-uri slices are not affected."""
    file = make_source(client, "file", uri=str(audio_dir / "album.flac"))
    for _ in range(2):  # identical slices are allowed (uri is null: NULLs are distinct)
        make_source(client, "slice", parent_id=file["id"], start_ms=0, end_ms=1000)
    url = "https://www.youtube.com/watch?v=x"
    link = make_source(client, "youtube", uri=url)
    response = client.post("/sources", json={"kind": "youtube", "uri": url})
    assert response.status_code == 409
    assert "uq_source_kind_uri" in response.json()["detail"]
    # The same URL under another kind is a different source; the one link can be reused
    # as the parent of several derived sources.
    make_source(client, "soundcloud", uri=url)
    make_source(client, "file", uri=str(audio_dir / "song.mp3"), parent_id=link["id"])
    make_source(client, "file", uri=str(audio_dir / "song.m4a"), parent_id=link["id"])
