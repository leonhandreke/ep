import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_artist, make_recording, make_release_group, make_track

COVER = (Path(__file__).parent / "fixtures" / "cover.png").read_bytes()


def search(client: TestClient, q: str, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def titles(hits: list[dict[str, Any]]) -> list[str]:
    return [hit["title"] for hit in hits]


def test_search_short_prefix_finds_accented_name(client: TestClient) -> None:
    bjork = make_artist(client, "Björk", disambiguation="Icelandic singer")
    make_artist(client, "Brant Bjork")
    make_artist(client, "Bob Dylan")
    for q in ("bjo", "Bjö", "BJORK", "björk"):
        hits = search(client, q, types="artist")
        assert hits[0]["id"] == bjork["id"], q
    assert search(client, "bjo", types="artist")[0] == {
        "type": "artist",
        "id": bjork["id"],
        "mbid": None,
        "title": "Björk",
        "subtitle": "Icelandic singer",
        "cover_url": None,
        "release_group_type": None,
        "date": None,
        "length_ms": None,
        "release_group_title": None,
        "track_count": None,
        "release_group_count": 0,
        "loose_recording_count": 0,
        "radio_match": None,
        # What the client writes into a note for it.
        "link_ref": bjork["id"],
        "link": f"[[artist:{bjork['id']}|Björk]]",
        "score": search(client, "bjo", types="artist")[0]["score"],
    }
    assert "Bob Dylan" not in titles(search(client, "bjo"))


def test_search_word_prefix_and_typos(client: TestClient) -> None:
    make_artist(client, "Nina Simone")
    make_artist(client, "Simon & Garfunkel")
    # The start of the name ranks above the start of a later word.
    assert titles(search(client, "sim")) == ["Simon & Garfunkel", "Nina Simone"]
    assert titles(search(client, "nina simon"))[0] == "Nina Simone"
    assert titles(search(client, "nian simone"))[0] == "Nina Simone"


def test_search_finds_words_with_dropped_letters(client: TestClient) -> None:
    bjork = make_artist(client, "Björk")
    make_release_group(client, bjork, "Homogenic")
    for other in ("Home Again", "Homegrown", "Hogan", "Gene"):
        make_release_group(client, bjork, other)
    assert titles(search(client, "homgen", types="release_group")) == ["Homogenic"]
    assert titles(search(client, "hmogenic", types="release_group")) == ["Homogenic"]
    assert search(client, "hmgn", types="release_group") == []  # too many dropped


def test_search_across_types_with_subtitles_and_covers(client: TestClient) -> None:
    nina = make_artist(client, "Nina Simone")
    pastel = make_release_group(client, nina, "Pastel Blues")
    cover_url = client.put(
        f"/covers/release-group/{pastel['id']}",
        content=COVER,
        headers={"Content-Type": "image/png"},
    ).json()["cover_url"]
    sinnerman = make_recording(client, nina, "Sinnerman")
    make_track(client, pastel, sinnerman)
    loose = make_recording(client, nina, "Sinnerman (live)")

    hits = search(client, "sinner")
    assert [(h["type"], h["title"]) for h in hits] == [
        ("recording", "Sinnerman"),
        ("recording", "Sinnerman (live)"),
    ]
    assert hits[0]["subtitle"] == "Nina Simone"
    assert hits[0]["cover_url"] == cover_url
    assert hits[1]["id"] == loose["id"]
    assert hits[1]["cover_url"] is None

    [rg] = search(client, "pastel")
    assert rg["type"] == "release_group"
    assert rg["subtitle"] == "Nina Simone"
    assert rg["cover_url"] == cover_url


def test_search_types_and_limit(client: TestClient) -> None:
    nina = make_artist(client, "Nina")
    make_release_group(client, nina, "Nina")
    make_recording(client, nina, "Nina")
    assert {h["type"] for h in search(client, "nina")} == {"artist", "release_group", "recording"}
    hits = search(client, "nina", types=["recording", "release_group"])
    assert {h["type"] for h in hits} == {"release_group", "recording"}
    assert len(search(client, "nina", limit=1)) == 1


def test_search_query_is_not_a_pattern(client: TestClient) -> None:
    make_artist(client, "Nina Simone")
    assert search(client, "%") == []
    assert search(client, "_") == []
    assert search(client, "   ") == []
    assert client.get("/search", params={"q": ""}).status_code == 422
    assert client.get("/search", params={"q": "x", "types": "track"}).status_code == 422


def test_resolve_by_internal_id_or_mbid(client: TestClient) -> None:
    mbid, release_mbid = str(uuid.uuid4()), str(uuid.uuid4())
    nina = make_artist(client, "Nina Simone", mbid=mbid)
    pastel = make_release_group(client, nina, mbid=str(uuid.uuid4()), release_mbid=release_mbid)
    recording = make_recording(client, nina)

    def resolve(type: str, key: str) -> Any:
        return client.get(f"/resolve/{type}/{key}")

    assert resolve("artist", nina["id"]).json() == {"type": "artist", "id": nina["id"]}
    assert resolve("artist", mbid).json() == {"type": "artist", "id": nina["id"]}
    for key in (pastel["id"], pastel["mbid"], release_mbid):
        assert resolve("release_group", key).json()["id"] == pastel["id"]
    assert resolve("recording", recording["id"]).json()["id"] == recording["id"]
    assert resolve("recording", nina["id"]).status_code == 404  # wrong type
    assert resolve("artist", str(uuid.uuid4())).status_code == 404
    assert resolve("track", nina["id"]).status_code == 422
    assert resolve("artist", "not-a-uuid").status_code == 422


def test_search_artist_and_title_together(client: TestClient) -> None:
    nina = make_artist(client, "Nina Simone")
    sinnerman = make_recording(client, nina, "Sinnerman")
    make_recording(client, nina, "Feeling Good")
    make_recording(client, make_artist(client, "Hozier"), "Sinnerman")
    pastel = make_release_group(client, nina, "Pastel Blues")
    for q in (
        "nina simone sinnerman",
        "sinnerman nina simone",
        "nina simone sinner",
        "simone sinnerman",
    ):
        hits = search(client, q)
        assert hits[0]["id"] == sinnerman["id"], q
    assert search(client, "nina simone pastel")[0]["id"] == pastel["id"]
    # One word: titles and names only, the artist first.
    assert [h["title"] for h in search(client, "nina")] == ["Nina Simone"]


def test_search_other_scripts_by_prefix(client: TestClient) -> None:
    # Postgres treats viramas and some Thai signs as separators (Python's normalize keeps
    # them); queries are normalised in Postgres like the stored names.
    for name, prefix in (
        ("ஸ்ரீநிவாஸ்", "ஸ்"),
        ("क्षितिज", "क्"),
        ("เบิร์ด ธงไชย", "เบิร์"),
        ("Пётр Чайковский", "пет"),
    ):
        make_artist(client, name)
        assert titles(search(client, prefix, types="artist")) == [name], name
