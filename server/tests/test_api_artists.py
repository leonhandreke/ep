import uuid

from fastapi.testclient import TestClient

from tests.helpers import credit, make_artist, make_recording, make_release_group, post


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
    assert response.json() == artist


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
    page = client.get("/artists", params={"limit": 1, "offset": 1}).json()
    assert [a["name"] for a in page] == ["The Beatles"]
    assert client.get("/artists", params={"limit": 0}).status_code == 422


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
