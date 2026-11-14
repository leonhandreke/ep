from fastapi.testclient import TestClient


def test_operation_ids_are_endpoint_names(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation_ids = [op["operationId"] for path in schema["paths"].values() for op in path.values()]
    assert len(operation_ids) == len(set(operation_ids))
    assert {
        "list_artists",
        "list_release_group_tracks",
        "add_recording_external_id",
        "list_recording_sources",
        "stream_source",
        "set_release_group_cover",
        "search_library",
        "resolve",
        "search_musicbrainz",
        "import_musicbrainz_release",
    } <= set(operation_ids)


def test_patch_schemas_do_not_make_required_fields_nullable(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    update = schemas["ArtistUpdate"]
    assert "required" not in update
    assert update["properties"]["name"] == {"type": "string", "minLength": 1, "title": "Name"}
    assert {"type": "null"} in update["properties"]["mbid"]["anyOf"]


def test_error_responses_are_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    error = {"$ref": "#/components/schemas/ErrorRead"}
    update = schema["paths"]["/release-groups/{id}"]["patch"]["responses"]
    assert update["404"]["content"]["application/json"]["schema"] == error
    assert update["409"]["content"]["application/json"]["schema"] == error
    assert "404" not in schema["paths"]["/artists"]["get"]["responses"]


def test_stream_is_documented_as_audio(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/sources/{id}/stream"]["get"]["responses"]
    assert set(responses["200"]["content"]) == {"audio/*"}
    assert set(responses["206"]["content"]) == {"audio/*"}
    assert {"403", "404", "409"} <= set(responses)
    cover_url = schema["components"]["schemas"]["ReleaseGroupRead"]["properties"]["cover_url"]
    assert cover_url["readOnly"] is True


def test_response_fields_with_defaults_are_required(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "playback" in schemas["SourceRead"]["required"]
    assert "playback" in schemas["TrackRead"]["required"]
    assert "cover_url" in schemas["ReleaseGroupRead"]["required"]
