import uuid

from fastapi.testclient import TestClient

from tests.helpers import (
    credit,
    make_artist,
    make_recording,
    make_release_group,
    make_track,
    post,
)


def test_create_and_get_artist(client: TestClient) -> None:
    mbid = str(uuid.uuid4())
    artist = make_artist(client, "  Nina Simone ", mbid=mbid, type="person")
    assert artist["name"] == "Nina Simone"
    assert artist["sort_name"] == "Nina Simone"  # defaults to the name
    assert artist["disambiguation"] == ""
    assert artist["mbid"] == mbid
    assert artist["type"] == "person"

    response = client.get(f"/artists/{artist['id']}")
    assert response.status_code == 200
    assert response.json() == {
        **artist,
        "counts": {
            "release_groups": 0,
            "loose_recordings": 0,
            "appearances": 0,
            "radio_episodes": 0,
        },
        "radio_only": False,
    }
    assert artist["link"] == f"[[artist:{mbid}|Nina Simone]]"


def test_create_artist_validation(client: TestClient) -> None:
    assert client.post("/artists", json={"name": " "}).status_code == 422
    assert client.post("/artists", json={"name": "X", "type": "robot"}).status_code == 422


def test_duplicate_mbid_conflicts(client: TestClient) -> None:
    mbid = str(uuid.uuid4())
    make_artist(client, "A", mbid=mbid)
    response = client.post("/artists", json={"name": "B", "mbid": mbid})
    assert response.status_code == 409
    assert "uq_artist_mbid" in response.json()["detail"]
    # The session is still usable after the conflict.
    make_artist(client, "C")


def test_get_missing_artist(client: TestClient) -> None:
    assert client.get(f"/artists/{uuid.uuid4()}").status_code == 404


def test_list_artists_by_sort_name_with_pagination(client: TestClient) -> None:
    make_artist(client, "The Beatles", sort_name="Beatles, The")
    make_artist(client, "Can")
    make_artist(client, "ABBA")
    names = [a["name"] for a in client.get("/artists").json()]
    assert names == ["ABBA", "The Beatles", "Can"]
    response = client.get("/artists", params={"limit": 1, "offset": 1})
    assert [a["name"] for a in response.json()] == ["The Beatles"]
    assert response.headers["X-Total-Count"] == "3"
    assert client.get("/artists", params={"q": "b"}).headers["X-Total-Count"] == "2"
    assert client.get("/artists", params={"limit": 0}).status_code == 422


def test_artist_position_in_the_list(client: TestClient) -> None:
    abba = make_artist(client, "ABBA")
    beatles = make_artist(client, "The Beatles", sort_name="Beatles, The")
    bjork = make_artist(client, "Björk")
    can = make_artist(client, "Can")

    def position(**params: str) -> dict:
        response = client.get("/artists/position", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    # Type-to-select: the first artist at or after the text, ignoring case and accents.
    assert position(sort_name="bjo") == {"offset": 2, "id": bjork["id"]}
    assert position(sort_name="B") == {"offset": 1, "id": beatles["id"]}
    assert position(sort_name="zz") == {"offset": 4, "id": None}
    # Navigate-to: where an artist is, in the list with the same filter.
    assert position(id=can["id"]) == {"offset": 3, "id": can["id"]}
    assert position(id=can["id"], q="c") == {"offset": 0, "id": can["id"]}
    assert position(sort_name="bk", q="a") == {"offset": 2, "id": can["id"]}  # no Björk
    response = client.get("/artists/position", params={"id": bjork["id"], "q": "c"})
    assert response.status_code == 404  # filtered out
    assert client.get("/artists/position").status_code == 422
    both = {"id": abba["id"], "sort_name": "a"}
    assert client.get("/artists/position", params=both).status_code == 422


def test_update_artist(client: TestClient) -> None:
    artist = make_artist(client, "Prince", mbid=str(uuid.uuid4()), type="person")
    response = client.patch(
        f"/artists/{artist['id']}", json={"disambiguation": "US singer", "mbid": None}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["disambiguation"] == "US singer"
    assert updated["mbid"] is None
    assert updated["name"] == "Prince"  # omitted fields are left alone
    assert updated["type"] == "person"
    assert updated["updated_at"] > artist["updated_at"]


def test_update_artist_rejects_null_for_required_field(client: TestClient) -> None:
    artist = make_artist(client)
    assert client.patch(f"/artists/{artist['id']}", json={"name": None}).status_code == 422
    assert client.patch(f"/artists/{uuid.uuid4()}", json={}).status_code == 404


def test_delete_artist(client: TestClient) -> None:
    artist = make_artist(client)
    post(client, f"/artists/{artist['id']}/external-ids", {"scheme": "spotify", "value": "x"})
    assert client.delete(f"/artists/{artist['id']}").status_code == 204
    assert client.get(f"/artists/{artist['id']}").status_code == 404
    assert client.delete(f"/artists/{artist['id']}").status_code == 404


def test_delete_credited_artist_conflicts(client: TestClient) -> None:
    artist = make_artist(client)
    recording = make_recording(client, artist)
    assert client.delete(f"/artists/{artist['id']}").status_code == 409
    # Once nothing uses the credit any more, the artist can go.
    assert client.delete(f"/recordings/{recording['id']}").status_code == 204
    assert client.delete(f"/artists/{artist['id']}").status_code == 204


def test_list_artist_release_groups(client: TestClient) -> None:
    nina = make_artist(client, "Nina Simone")
    other = make_artist(client, "Someone Else")
    make_release_group(client, nina, "Undated")
    make_release_group(client, nina, "Pastel Blues", date="1965-10")
    make_release_group(client, nina, "Little Girl Blue", date="1959")
    post(
        client,
        "/release-groups",
        {"title": "Duets", "artist_credit": credit(other, nina), "date": "1970"},
    )
    make_release_group(client, other, "Not Nina's")

    response = client.get(f"/artists/{nina['id']}/release-groups")
    assert response.status_code == 200
    titles = [rg["title"] for rg in response.json()]
    assert titles == ["Little Girl Blue", "Pastel Blues", "Duets", "Undated"]
    page = client.get(f"/artists/{nina['id']}/release-groups", params={"limit": 2, "offset": 1})
    assert [rg["title"] for rg in page.json()] == ["Pastel Blues", "Duets"]
    assert client.get(f"/artists/{uuid.uuid4()}/release-groups").status_code == 404


def test_list_artist_recordings(client: TestClient) -> None:
    nina = make_artist(client, "Nina Simone")
    other = make_artist(client, "Someone Else")
    release_group = make_release_group(client, nina)
    on_album = make_recording(client, nina, "Sinnerman")
    post(
        client,
        "/tracks",
        {"release_group_id": release_group["id"], "recording_id": on_album["id"], "position": 1},
    )
    make_recording(client, nina, "Loose Track")
    post(client, "/recordings", {"title": "Duet", "artist_credit": credit(other, nina)})
    make_recording(client, other, "Not Nina's")

    def titles(**params: int) -> list[str]:
        response = client.get(f"/artists/{nina['id']}/recordings", params=params)
        assert response.status_code == 200, response.text
        return [recording["title"] for recording in response.json()]

    assert titles() == ["Duet", "Loose Track", "Sinnerman"]
    assert titles(standalone=True) == ["Duet", "Loose Track"]
    assert titles(standalone=False) == ["Sinnerman"]
    assert titles(standalone=True, limit=1, offset=1) == ["Loose Track"]
    assert client.get(f"/artists/{uuid.uuid4()}/recordings").status_code == 404


def test_list_artist_appearances(client: TestClient) -> None:
    bonobo = make_artist(client, "Bonobo")
    rhye = make_artist(client, "Rhye")
    migration = make_release_group(client, bonobo, "Migration", date="2017")
    black_sands = make_release_group(client, bonobo, "Black Sands", date="2010")
    woman = make_release_group(client, rhye, "Woman", date="2013")
    featured = post(
        client, "/recordings", {"title": "Break Apart", "artist_credit": credit(bonobo, rhye)}
    )
    make_track(client, migration, featured, 1)
    make_track(
        client,
        migration,
        post(client, "/recordings", {"title": "Second", "artist_credit": credit(bonobo, rhye)}),
        2,
    )
    make_track(client, black_sands, make_recording(client, bonobo, "Kiara"))
    make_track(client, woman, featured)  # Rhye's own album: not an appearance

    def titles(artist: dict) -> list[str]:
        response = client.get(f"/artists/{artist['id']}/appearances")
        assert response.status_code == 200, response.text
        return [rg["title"] for rg in response.json()]

    assert titles(rhye) == ["Migration"]
    assert titles(bonobo) == ["Woman"]
    rhye_counts = client.get(f"/artists/{rhye['id']}").json()["counts"]
    assert rhye_counts["appearances"] == 1
    assert rhye_counts["release_groups"] == 1
    assert client.get(f"/artists/{uuid.uuid4()}/appearances").status_code == 404
