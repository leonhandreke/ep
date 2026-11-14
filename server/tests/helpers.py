"""Create catalogue entities through the API, for tests."""

from typing import Any

from fastapi.testclient import TestClient


def post(client: TestClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def make_artist(client: TestClient, name: str = "Nina Simone", **fields: Any) -> dict[str, Any]:
    return post(client, "/artists", {"name": name, **fields})


def credit(*artists: dict[str, Any], join: str = " & ") -> list[dict[str, Any]]:
    """An artist credit input for these artists, joined by ``join``."""
    return [
        {"artist_id": artist["id"], "join_phrase": join if i < len(artists) - 1 else ""}
        for i, artist in enumerate(artists)
    ]


def make_release_group(
    client: TestClient, artist: dict[str, Any], title: str = "Pastel Blues", **fields: Any
) -> dict[str, Any]:
    return post(
        client, "/release-groups", {"title": title, "artist_credit": credit(artist), **fields}
    )


def make_recording(
    client: TestClient, artist: dict[str, Any], title: str = "Sinnerman", **fields: Any
) -> dict[str, Any]:
    return post(client, "/recordings", {"title": title, "artist_credit": credit(artist), **fields})


def make_track(
    client: TestClient,
    release_group: dict[str, Any],
    recording: dict[str, Any],
    position: int = 1,
    **fields: Any,
) -> dict[str, Any]:
    return post(
        client,
        "/tracks",
        {
            "release_group_id": release_group["id"],
            "recording_id": recording["id"],
            "position": position,
            **fields,
        },
    )
