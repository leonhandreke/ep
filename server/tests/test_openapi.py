from fastapi.testclient import TestClient


def test_operation_ids_are_endpoint_names(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation_ids = [op["operationId"] for path in schema["paths"].values() for op in path.values()]
    assert len(operation_ids) == len(set(operation_ids))
    assert {"list_artists", "list_release_group_tracks", "add_recording_external_id"} <= set(
        operation_ids
    )


def test_patch_schemas_do_not_make_required_fields_nullable(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    update = schemas["ArtistUpdate"]
    assert "required" not in update
    assert update["properties"]["name"] == {"type": "string", "minLength": 1, "title": "Name"}
    assert {"type": "null"} in update["properties"]["mbid"]["anyOf"]
