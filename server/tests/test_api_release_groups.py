import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.models import ArtistCredit
from tests.helpers import credit, make_artist, make_recording, make_release_group, make_track


def test_create_release_group_with_credit(client: TestClient) -> None:
    simon = make_artist(client, "Paul Simon")
    garfunkel = make_artist(client, "Art Garfunkel")
    response = client.post(
        "/release-groups",
        json={
            "title": "Bookends",
            "type": "album",
            "date": "1968-04-03",
            "barcode": "074640914914",
            "mbid": str(uuid.uuid4()),
            "release_mbid": str(uuid.uuid4()),
            "artist_credit": [
                {"artist_id": simon["id"], "credited_name": "Simon", "join_phrase": " & "},
                {"artist_id": garfunkel["id"], "credited_name": "Garfunkel"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    release_group = response.json()
    assert release_group["artist_credit"]["name"] == "Simon & Garfunkel"
    assert release_group["artist_credit"]["names"] == [
        {"artist_id": simon["id"], "credited_name": "Simon", "join_phrase": " & "},
        {"artist_id": garfunkel["id"], "credited_name": "Garfunkel", "join_phrase": ""},
    ]
    assert release_group["date"] == "1968-04-03"
    detail = client.get(f"/release-groups/{release_group['id']}").json()
    assert detail == {
        **release_group,
        "track_count": 0,
        "disc_count": 0,
        "length_ms": 0,
        "play_states": dict.fromkeys(
            ["file", "slice", "radio_excerpt", "file_missing", "link_only", "no_source"], 0
        ),
        "link_kinds": [],
    }


def test_credit_defaults_to_artist_name_and_is_shared(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    first = make_release_group(client, nina, "One")
    second = make_release_group(client, nina, "Two", type="ep")
    assert first["artist_credit"]["name"] == "Nina Simone"
    assert first["artist_credit"]["id"] == second["artist_credit"]["id"]
    assert second["type"] == "ep"
    assert first["type"] == "album"  # default
    assert len(session.exec(select(ArtistCredit)).all()) == 1


def test_create_release_group_validation(client: TestClient) -> None:
    nina = make_artist(client)

    def create(**fields: object) -> int:
        body = {"title": "X", "artist_credit": credit(nina), **fields}
        return client.post("/release-groups", json=body).status_code

    assert create() == 201
    assert create(artist_credit=[]) == 422
    assert create(artist_credit=[{"artist_id": str(uuid.uuid4())}]) == 422
    assert create(date="1965-13") == 422
    assert create(date="1965-02-30") == 422
    assert create(date="65") == 422
    assert create(date="1965-02") == 201
    assert create(type="bootleg") == 422


def test_duplicate_release_mbid_conflicts(client: TestClient) -> None:
    nina = make_artist(client)
    release_mbid = str(uuid.uuid4())
    make_release_group(client, nina, "One", release_mbid=release_mbid)
    response = client.post(
        "/release-groups",
        json={"title": "Two", "artist_credit": credit(nina), "release_mbid": release_mbid},
    )
    assert response.status_code == 409


def test_update_release_group(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    other = make_artist(client, "Other")
    release_group = make_release_group(client, nina, date="1965")
    response = client.patch(
        f"/release-groups/{release_group['id']}",
        json={"title": "Pastel Blues (Remaster)", "date": None, "artist_credit": credit(other)},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Pastel Blues (Remaster)"
    assert updated["date"] is None
    assert updated["artist_credit"]["name"] == "Other"
    # The old credit was used only here, so it is gone.
    assert [c.name for c in session.exec(select(ArtistCredit))] == ["Other"]

    for body in ({"title": None}, {"type": None}, {"artist_credit": None}, {"date": "x"}):
        response = client.patch(f"/release-groups/{release_group['id']}", json=body)
        assert response.status_code == 422, body
    assert client.patch(f"/release-groups/{uuid.uuid4()}", json={}).status_code == 404


def test_delete_release_group_keeps_recordings(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    release_group = make_release_group(client, nina)
    recording = make_recording(client, nina)
    track = make_track(client, release_group, recording)

    assert client.delete(f"/release-groups/{release_group['id']}").status_code == 204
    assert client.get(f"/release-groups/{release_group['id']}").status_code == 404
    assert client.get(f"/tracks/{track['id']}").status_code == 404
    assert client.get(f"/recordings/{recording['id']}").json()["tracks"] == []
    # The credit is still used by the recording.
    assert len(session.exec(select(ArtistCredit)).all()) == 1
    assert client.delete(f"/release-groups/{release_group['id']}").status_code == 404


def test_list_tracks_in_disc_and_position_order(client: TestClient) -> None:
    nina = make_artist(client)
    guest = make_artist(client, "Guest")
    release_group = make_release_group(client, nina)
    for disc, position, title in [(2, 1, "C"), (1, 2, "B"), (1, 1, "A")]:
        recording = make_recording(client, nina, title, length_ms=1000)
        make_track(client, release_group, recording, position, disc=disc)
    duet = client.post(
        "/recordings", json={"title": "Duet", "artist_credit": credit(nina, guest)}
    ).json()
    make_track(client, release_group, duet, 2, disc=2, title="Duet (Live)")

    response = client.get(f"/release-groups/{release_group['id']}/tracks")
    assert response.status_code == 200
    tracks = response.json()
    assert [(t["disc"], t["position"], t["recording"]["title"]) for t in tracks] == [
        (1, 1, "A"),
        (1, 2, "B"),
        (2, 1, "C"),
        (2, 2, "Duet"),
    ]
    assert tracks[0]["title"] is None
    assert tracks[0]["recording"]["length_ms"] == 1000
    assert tracks[3]["title"] == "Duet (Live)"
    assert tracks[3]["recording"]["artist_credit"]["name"] == "Nina Simone & Guest"

    page = client.get(f"/release-groups/{release_group['id']}/tracks", params={"offset": 3})
    assert [t["recording"]["title"] for t in page.json()] == ["Duet"]
    assert client.get(f"/release-groups/{uuid.uuid4()}/tracks").status_code == 404


def test_conflict_while_changing_credit_is_409(client: TestClient, session: Session) -> None:
    # The new credit is looked up with the conflicting MBID already pending, so the
    # violation surfaces on autoflush, not on commit.
    nina = make_artist(client)
    other = make_artist(client, "Other")
    mbid = str(uuid.uuid4())
    make_release_group(client, nina, "One", mbid=mbid)
    two = make_release_group(client, nina, "Two")
    response = client.patch(
        f"/release-groups/{two['id']}", json={"mbid": mbid, "artist_credit": credit(other)}
    )
    assert response.status_code == 409
    assert "uq_release_group_mbid" in response.json()["detail"]
    unchanged = client.get(f"/release-groups/{two['id']}").json()
    assert (unchanged["mbid"], unchanged["artist_credit"]["name"]) == (None, "Nina Simone")
    assert [c.name for c in session.exec(select(ArtistCredit))] == ["Nina Simone"]


def test_unknown_credited_artist_is_a_validation_error(client: TestClient) -> None:
    nina = make_artist(client)
    missing = str(uuid.uuid4())
    body = {"title": "X", "artist_credit": [{"artist_id": nina["id"]}, {"artist_id": missing}]}
    response = client.post("/release-groups", json=body)
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "artist_credit", 1, "artist_id"]
    assert error["input"] == missing
