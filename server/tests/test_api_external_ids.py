import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.models import ExternalId
from tests.helpers import make_artist, make_recording, make_release_group

type Factory = Callable[[TestClient], dict[str, Any]]

ENTITIES: dict[str, Factory] = {
    "artists": make_artist,
    "release-groups": lambda client: make_release_group(client, make_artist(client)),
    "recordings": lambda client: make_recording(client, make_artist(client)),
}


@pytest.mark.parametrize("path", ENTITIES)
def test_external_ids(client: TestClient, path: str) -> None:
    entity = ENTITIES[path](client)
    other = ENTITIES[path](client)
    url = f"/{path}/{entity['id']}/external-ids"

    assert client.get(url).json() == []
    response = client.post(url, json={"scheme": "isrc", "value": "USRC17607839"})
    assert response.status_code == 201
    isrc = response.json()
    assert isrc["scheme"] == "isrc"
    assert isrc["value"] == "USRC17607839"
    assert isrc["entity_id"] == entity["id"]
    deezer = client.post(url, json={"scheme": "deezer", "value": "3135556"}).json()
    assert client.get(url).json() == [deezer, isrc]  # by scheme, then value

    assert client.post(url, json={"scheme": "isrc", "value": "USRC17607839"}).status_code == 409
    assert client.post(url, json={"scheme": "Not A Scheme", "value": "x"}).status_code == 422
    assert client.post(url, json={"scheme": "isrc", "value": ""}).status_code == 422

    # Removing via another entity's URL does not work.
    other_url = f"/{path}/{other['id']}/external-ids"
    assert client.delete(f"{other_url}/{isrc['id']}").status_code == 404
    assert client.delete(f"{url}/{isrc['id']}").status_code == 204
    assert client.delete(f"{url}/{isrc['id']}").status_code == 404
    assert client.get(url).json() == [deezer]

    missing = f"/{path}/{uuid.uuid4()}/external-ids"
    assert client.get(missing).status_code == 404
    assert client.post(missing, json={"scheme": "isrc", "value": "x"}).status_code == 404


@pytest.mark.parametrize("path", ENTITIES)
def test_external_ids_deleted_with_entity(client: TestClient, session: Session, path: str) -> None:
    entity = ENTITIES[path](client)
    client.post(f"/{path}/{entity['id']}/external-ids", json={"scheme": "spotify", "value": "x"})
    assert client.delete(f"/{path}/{entity['id']}").status_code == 204
    assert session.exec(select(ExternalId)).all() == []
