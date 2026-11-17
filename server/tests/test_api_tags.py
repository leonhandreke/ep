"""Tags: add by name (find or create, ignoring case), remove, list with counts and by
prefix (by name or most used first), rename and merge, delete. Everything with a tag:
``test_api_search``."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.models import EntityTag, Tag
from tests.helpers import make_artist, make_release_group

MBID = "8f3471b5-7e6a-48da-86a9-c1c07a0f47ae"


def add(client: TestClient, type: str, id: str, name: str) -> list[dict[str, Any]]:
    response = client.post(f"/entity-tags/{type}/{id}", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def names(tags: list[dict[str, Any]]) -> list[str]:
    return [tag["name"] for tag in tags]


def test_add_find_or_create_ignoring_case(client: TestClient, session: Session) -> None:
    nina = make_artist(client, mbid=MBID)
    album = make_release_group(client, nina)
    [tag] = add(client, "artist", nina["id"], "  Soul \t Jazz ")
    assert (tag["name"], tag["count"]) == ("Soul Jazz", 1)
    # By MBID; the same tag ignoring case, spelt as first written; adding twice is fine.
    assert add(client, "artist", MBID, "soul jazz") == [tag]
    tags = add(client, "release_group", album["id"], "SOUL JAZZ")
    assert tags[0]["count"] == 2
    add(client, "release_group", album["id"], "Ballads")
    assert names(client.get(f"/entity-tags/release_group/{album['id']}").json()) == [
        "Ballads",
        "Soul Jazz",
    ]
    assert len(session.exec(select(Tag)).all()) == 2
    for bad in "", "   ", "x" * 101:
        response = client.post(f"/entity-tags/artist/{nina['id']}", json={"name": bad})
        assert response.status_code == 422, bad
    assert client.post(f"/entity-tags/artist/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_remove(client: TestClient) -> None:
    nina = make_artist(client)
    [tag] = add(client, "artist", nina["id"], "soul")
    [jazz] = [t for t in add(client, "artist", nina["id"], "jazz") if t["name"] == "jazz"]
    path = f"/entity-tags/artist/{nina['id']}/{tag['id']}"
    # Answers with the entity's tags, as adding does.
    response = client.delete(path)
    assert response.status_code == 200
    assert response.json() == [jazz]
    assert client.delete(path).status_code == 404
    # The tag stays, unused.
    assert client.get(f"/tags/{tag['id']}").json() == {**tag, "count": 0}


def test_list_with_counts_and_prefix(client: TestClient) -> None:
    nina = make_artist(client)
    other = make_artist(client, "Other")
    for artist in nina, other:
        add(client, "artist", artist["id"], "Jazz")
    add(client, "artist", nina["id"], "jazz-funk")
    add(client, "artist", nina["id"], "Ambient")
    tags = client.get("/tags").json()
    assert [(t["name"], t["count"]) for t in tags] == [
        ("Ambient", 1),
        ("Jazz", 2),
        ("jazz-funk", 1),
    ]
    assert names(client.get("/tags", params={"prefix": "JA"}).json()) == ["Jazz", "jazz-funk"]
    # No LIKE wildcards.
    assert client.get("/tags", params={"prefix": "%"}).json() == []
    assert names(client.get("/tags", params={"limit": 1, "offset": 1}).json()) == ["Jazz"]
    # Most used first, then by name (tag suggestions).
    by_count = client.get("/tags", params={"prefix": "j", "sort": "count"}).json()
    assert names(by_count) == ["Jazz", "jazz-funk"]
    assert names(client.get("/tags", params={"sort": "count"}).json()) == [
        "Jazz",
        "Ambient",
        "jazz-funk",
    ]


def test_rename_and_merge(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    other = make_artist(client, "Other")
    [jazz] = add(client, "artist", nina["id"], "jazz")
    add(client, "artist", other["id"], "Jazz Music")
    music = next(t for t in add(client, "artist", nina["id"], "jazz music") if t["name"] != "jazz")
    # A plain rename (also just of the case).
    response = client.patch(f"/tags/{jazz['id']}", json={"name": "Jazz"})
    assert response.json() == {"id": jazz["id"], "name": "Jazz", "count": 1}
    # Renaming onto an existing name merges: Nina had both, Other only "Jazz Music".
    response = client.patch(f"/tags/{music['id']}", json={"name": " JAZZ "})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": jazz["id"], "name": "JAZZ", "count": 2}
    assert session.get(Tag, uuid.UUID(music["id"])) is None
    for artist in nina, other:
        tags = client.get(f"/entity-tags/artist/{artist['id']}").json()
        assert names(tags) == ["JAZZ"]
    assert len(session.exec(select(EntityTag)).all()) == 2
    assert client.patch(f"/tags/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_delete(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    [tag] = add(client, "artist", nina["id"], "soul")
    assert client.delete(f"/tags/{tag['id']}").status_code == 204
    assert client.get(f"/entity-tags/artist/{nina['id']}").json() == []
    assert session.exec(select(EntityTag)).all() == []
    assert client.delete(f"/tags/{tag['id']}").status_code == 404
