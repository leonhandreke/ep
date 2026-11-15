import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from tests.helpers import make_artist, make_release_group

PNG = (Path(__file__).parent / "fixtures" / "cover.png").read_bytes()
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 16


def test_upload_get_replace_and_delete_cover(client: TestClient, library_dir: Path) -> None:
    release_group = make_release_group(client, make_artist(client))
    id = release_group["id"]
    url = f"/covers/release-group/{id}"
    assert release_group["cover_url"] is None
    assert "cover_path" not in release_group
    assert client.get(url).status_code == 404

    response = client.put(url, content=PNG, headers={"Content-Type": "image/png"})
    assert response.status_code == 200
    assert response.json()["cover_url"] == url
    assert client.get(f"/release-groups/{id}").json()["cover_url"] == url
    stored = library_dir / "_covers" / "release-group" / f"{id}.png"
    assert stored.read_bytes() == PNG

    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG

    # Replacing with another format removes the old file.
    assert client.put(url, content=JPEG, headers={"Content-Type": "image/jpeg"}).status_code == 200
    assert not stored.exists()
    assert client.get(url).headers["content-type"] == "image/jpeg"

    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert client.get(f"/release-groups/{id}").json()["cover_url"] is None
    assert list((library_dir / "_covers" / "release-group").iterdir()) == []


def test_cover_upload_must_be_an_image(client: TestClient) -> None:
    release_group = make_release_group(client, make_artist(client))
    url = f"/covers/release-group/{release_group['id']}"
    response = client.put(url, content=b"not an image", headers={"Content-Type": "image/png"})
    assert response.status_code == 415
    assert (
        client.put(
            f"/covers/release-group/{uuid.uuid4()}",
            content=PNG,
            headers={"Content-Type": "image/png"},
        ).status_code
        == 404
    )


def test_deleting_release_group_deletes_its_cover(client: TestClient, library_dir: Path) -> None:
    release_group = make_release_group(client, make_artist(client))
    url = f"/covers/release-group/{release_group['id']}"
    client.put(url, content=PNG, headers={"Content-Type": "image/png"})
    assert client.delete(f"/release-groups/{release_group['id']}").status_code == 204
    assert list((library_dir / "_covers" / "release-group").iterdir()) == []
