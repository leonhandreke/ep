import json

from fastapi.testclient import TestClient

from ep.openapi import PATH, document


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
        "resolve_entity",
        "get_health",
        "search_musicbrainz",
        "import_from_musicbrainz",
        "get_server",
        "classify_link",
        "get_imports_summary",
        "cancel_import_job",
        "stream_import_track",
        "set_release_group_track_order",
        "create_import_job",
        "list_import_jobs",
        "get_import_job",
        "review_import_job",
        "retry_import_job",
        "dismiss_import_job",
        "create_watch_dir",
        "scan_watch_dir",
        "get_note",
        "save_note",
        "search_notes",
        "list_backlinks",
        "get_entity_details",
        "list_tags",
        "update_tag",
        "list_tag_entities",
        "list_entity_tags",
        "add_entity_tag",
        "remove_entity_tag",
    } <= set(operation_ids)


def test_note_conflict_is_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/notes/{type}/{id}"]["put"]["responses"]
    conflict = responses["409"]["content"]["application/json"]["schema"]
    assert conflict == {"$ref": "#/components/schemas/NoteConflictRead"}
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NoteRead"
    }
    schemas = schema["components"]["schemas"]
    assert set(schemas["NoteRead"]["required"]) == set(schemas["NoteRead"]["properties"])
    assert {"link", "link_ref"} <= set(schemas["SearchHitRead"]["required"])


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


def test_paged_lists_document_their_total(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paged = [
        (path, method)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if any(p["name"] == "offset" for p in operation.get("parameters", []))
    ]
    assert ("/artists/{id}/release-groups", "get") in paged
    for path, method in paged:
        headers = schema["paths"][path][method]["responses"]["200"].get("headers", {})
        assert headers.get("X-Total-Count", {}).get("required") is True, path


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
    assert "playability" in schemas["TrackRead"]["required"]
    assert "host_path" in schemas["SourceRead"]["required"]
    assert "cover_url" in schemas["ReleaseGroupRead"]["required"]


def test_import_schemas_describe_issues_and_choices(client: TestClient) -> None:
    """The review UI is generated from these: enums and required fields, not free text."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["IssueKind"]["enum"]) == {
        "uncertain_artist",
        "uncertain_release_group",
        "uncertain_recording",
        "uncertain_metadata",
        "release_conflict",
        "metadata_not_found",
        "fetch_failed",
        "unexpected_response",
        "unreadable_file",
        "download_failed",
    }
    assert set(schemas["Choice"]["enum"]) == {
        "pick_candidate",
        "create_new",
        "keep_as_entered",
        "skip",
        "retry",
    }
    issue = schemas["Issue"]
    assert set(issue["required"]) == set(issue["properties"])
    assert issue["properties"]["choices"]["items"] == {"$ref": "#/components/schemas/Choice"}
    assert set(schemas["ImportJobRead"]["required"]) >= {"proposal", "log", "actions"}
    # Where an item goes, and covers: unions discriminated by ``kind``.
    target = schemas["Target"]
    assert target["discriminator"] == {
        "propertyName": "kind",
        "mapping": {
            kind: f"#/components/schemas/{name}Target"
            for kind, name in [
                ("existing", "Existing"),
                ("musicbrainz", "MusicBrainz"),
                ("new", "New"),
                ("skip", "Skip"),
                ("undecided", "Undecided"),
            ]
        },
    }
    assert set(schemas["ExistingTarget"]["required"]) == {"kind", "id", "label"}
    assert schemas["MusicBrainzTarget"]["properties"]["mbid"]["format"] == "uuid"
    cover = schemas["IncomingCover"]
    assert set(cover["discriminator"]["mapping"]) == {"embedded", "file", "url"}
    assert set(schemas["UrlCover"]["required"]) == {"kind", "url"}
    # The radio part of the proposal (NTS).
    radio = schemas["IncomingRadio"]
    assert set(radio["required"]) == {"show", "episode", "medium_key", "entries"}
    assert "radio" in schemas["Proposal"]["required"]


def test_text_ranges_say_utf16_and_note_base_is_optional(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "UTF-16" in schemas["TextRange"]["description"]
    assert "UTF-16" in schemas["NoteLinkRead"]["properties"]["range"]["description"]
    assert "UTF-16" in schemas["SnippetRead"]["properties"]["highlights"]["description"]
    # A generated client leaves out nil fields.
    assert schemas["NoteWrite"]["required"] == ["body"]
    details = schemas["EntityDetailsRead"]
    assert set(details["required"]) == set(details["properties"])


def test_committed_document_is_current(client: TestClient) -> None:
    """``server/openapi.json`` is what the server serves: regenerate it with
    ``uv run python -m ep.openapi`` after changing the API."""
    assert PATH.read_text() == document(), "openapi.json is stale: uv run python -m ep.openapi"
    assert json.loads(PATH.read_text()) == client.get("/openapi.json").json()


def test_no_generated_names_or_inline_bodies(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert not [name for name in schema["components"]["schemas"] if name.startswith("Body_")]
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert operation["operationId"], (method, path)
            for media_type, content in operation.get("requestBody", {}).get("content", {}).items():
                if media_type == "application/json":
                    assert "$ref" in content["schema"], (method, path)
    cover = schema["paths"]["/covers/release-group/{id}"]["put"]["requestBody"]["content"]
    assert set(cover) == {"image/jpeg", "image/png", "image/webp"}


def test_timestamps_and_clearing_are_described(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    created_at = schemas["ArtistRead"]["properties"]["created_at"]
    assert (created_at["type"], created_at["format"]) == ("string", "date-time")
    assert "null" in schemas["RecordingUpdate"]["description"]
    preferred = schemas["RecordingUpdate"]["properties"]["preferred_source_id"]
    assert {"type": "null"} in preferred["anyOf"]
