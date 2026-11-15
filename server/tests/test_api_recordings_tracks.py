import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.models import ArtistCredit
from tests.helpers import credit, make_artist, make_recording, make_release_group, make_track


def test_create_and_get_recording(client: TestClient) -> None:
    nina = make_artist(client)
    mbid = str(uuid.uuid4())
    recording = make_recording(client, nina, mbid=mbid, length_ms=622_000)
    assert recording["mbid"] == mbid
    assert recording["length_ms"] == 622_000
    assert recording["artist_credit"]["name"] == "Nina Simone"

    response = client.get(f"/recordings/{recording['id']}")
    assert response.status_code == 200
    no_source = {
        "state": "no_source",
        "playback": None,
        "radio_entry": None,
        "link": None,
        "missing_file_source_id": None,
    }
    # A standalone recording, metadata only.
    assert response.json() == {**recording, "tracks": [], "playability": no_source}
    assert client.get(f"/recordings/{uuid.uuid4()}").status_code == 404


def test_recording_lists_its_tracks(client: TestClient) -> None:
    nina = make_artist(client)
    recording = make_recording(client, nina)
    undated = make_release_group(client, nina, "Bootleg")
    best_of = make_release_group(client, nina, "Best Of", type="compilation", date="1990")
    pastel = make_release_group(client, nina, "Pastel Blues", date="1965-10")
    make_track(client, best_of, recording, 3, title="Sinnerman (Edit)")
    make_track(client, undated, recording, 2, disc=2)
    make_track(client, undated, recording, 1, disc=2)
    make_track(client, pastel, recording, 10)

    # By release group, oldest first and undated last, then disc and position.
    tracks = client.get(f"/recordings/{recording['id']}").json()["tracks"]
    assert [(t["release_group"]["title"], t["position"], t["title"]) for t in tracks] == [
        ("Pastel Blues", 10, None),
        ("Best Of", 3, "Sinnerman (Edit)"),
        ("Bootleg", 1, None),
        ("Bootleg", 2, None),
    ]


def test_create_recording_validation(client: TestClient) -> None:
    nina = make_artist(client)
    body = {"title": "X", "artist_credit": credit(nina)}
    assert client.post("/recordings", json={**body, "length_ms": -1}).status_code == 422
    assert client.post("/recordings", json={**body, "title": ""}).status_code == 422
    assert client.post("/recordings", json={"title": "X"}).status_code == 422


def test_update_recording(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    other = make_artist(client, "Other")
    recording = make_recording(client, nina, length_ms=1)
    response = client.patch(
        f"/recordings/{recording['id']}",
        json={
            "length_ms": None,
            "artist_credit": [{"artist_id": other["id"], "credited_name": "O"}],
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["length_ms"] is None
    assert updated["title"] == "Sinnerman"
    assert updated["artist_credit"]["name"] == "O"
    assert [c.name for c in session.exec(select(ArtistCredit))] == ["O"]
    assert client.patch(f"/recordings/{recording['id']}", json={"title": None}).status_code == 422
    assert client.patch(f"/recordings/{uuid.uuid4()}", json={}).status_code == 404


def test_delete_recording(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    recording = make_recording(client, nina)
    release_group = make_release_group(client, nina)
    track = make_track(client, release_group, recording)

    assert client.delete(f"/recordings/{recording['id']}").status_code == 409
    assert client.delete(f"/tracks/{track['id']}").status_code == 204
    assert client.delete(f"/recordings/{recording['id']}").status_code == 204
    assert client.get(f"/recordings/{recording['id']}").status_code == 404
    assert client.delete(f"/recordings/{recording['id']}").status_code == 404
    # The release group still uses the credit.
    assert len(session.exec(select(ArtistCredit)).all()) == 1


def test_create_and_get_track(client: TestClient) -> None:
    nina = make_artist(client)
    release_group = make_release_group(client, nina)
    recording = make_recording(client, nina)
    track = make_track(client, release_group, recording, 0)  # pregap tracks are position 0
    assert track["disc"] == 1
    assert track["position"] == 0
    assert track["release_group_id"] == release_group["id"]
    assert track["recording"] == recording
    assert client.get(f"/tracks/{track['id']}").json() == track
    assert client.get(f"/tracks/{uuid.uuid4()}").status_code == 404


def test_create_track_validation(client: TestClient) -> None:
    nina = make_artist(client)
    release_group = make_release_group(client, nina)
    recording = make_recording(client, nina)
    body = {"release_group_id": release_group["id"], "recording_id": recording["id"]}

    def create(**fields: object) -> int:
        return client.post("/tracks", json={**body, "position": 1, **fields}).status_code

    assert create(disc=0) == 422
    assert create(position=-1) == 422
    assert create(release_group_id=str(uuid.uuid4())) == 422
    assert create(recording_id=str(uuid.uuid4())) == 422
    assert create() == 201
    assert create() == 409  # same disc and position
    assert create(disc=2) == 201


def test_update_track(client: TestClient) -> None:
    nina = make_artist(client)
    release_group = make_release_group(client, nina)
    first = make_recording(client, nina, "First")
    second = make_recording(client, nina, "Second")
    track = make_track(client, release_group, first, 1, title="Override")
    make_track(client, release_group, first, 2)

    response = client.patch(
        f"/tracks/{track['id']}", json={"recording_id": second["id"], "title": None, "disc": 2}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["recording"]["title"] == "Second"
    assert updated["title"] is None
    assert (updated["disc"], updated["position"]) == (2, 1)

    url = f"/tracks/{track['id']}"
    assert client.patch(url, json={"disc": 1, "position": 2}).status_code == 409
    assert client.patch(url, json={"recording_id": str(uuid.uuid4())}).status_code == 422
    assert client.patch(url, json={"position": None}).status_code == 422
    assert client.patch(f"/tracks/{uuid.uuid4()}", json={}).status_code == 404


def test_delete_track(client: TestClient) -> None:
    nina = make_artist(client)
    track = make_track(client, make_release_group(client, nina), make_recording(client, nina))
    assert client.delete(f"/tracks/{track['id']}").status_code == 204
    assert client.delete(f"/tracks/{track['id']}").status_code == 404


def test_unknown_track_reference_is_a_validation_error(client: TestClient) -> None:
    nina = make_artist(client)
    track = make_track(client, make_release_group(client, nina), make_recording(client, nina))
    missing = str(uuid.uuid4())
    response = client.patch(f"/tracks/{track['id']}", json={"recording_id": missing})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "recording_id"]
