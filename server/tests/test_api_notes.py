"""Notes: reading and saving, links resolved by MBID or ID, broken links, optimistic
concurrency, backlinks with snippets, full-text search."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ep.models import EntityTag, Note, NoteLink, RadioEpisode, RadioShow, RadioStation
from tests.helpers import make_artist, make_recording, make_release_group, make_track

MBID = "8f3471b5-7e6a-48da-86a9-c1c07a0f47ae"
COVER = (Path(__file__).parent / "fixtures" / "cover.png").read_bytes()


def get_note(client: TestClient, type: str, id: str) -> dict[str, Any]:
    response = client.get(f"/notes/{type}/{id}")
    assert response.status_code == 200, response.text
    return response.json()


def put_note(
    client: TestClient, type: str, id: str, body: str, base: str | None = None
) -> dict[str, Any]:
    response = client.put(f"/notes/{type}/{id}", json={"body": body, "base_updated_at": base})
    assert response.status_code == 200, response.text
    return response.json()


def write(client: TestClient, type: str, id: str, body: str) -> dict[str, Any]:
    """Save a note over whatever is there."""
    return put_note(client, type, id, body, get_note(client, type, id)["updated_at"])


def radio(session: Session) -> tuple[RadioShow, RadioEpisode]:
    show = RadioShow(station=RadioStation.nts, slug="early-bird", name="Early Bird Show")
    session.add(show)
    episode = RadioEpisode(
        show_id=show.id,
        slug="ep-1",
        name="Early Bird Show w/ Pam",
        url="https://www.nts.live/shows/early-bird/episodes/ep-1",
        broadcast_at=datetime(2026, 11, 12, 7, tzinfo=UTC),
    )
    session.add(episode)
    session.flush()
    return show, episode


def test_missing_note_is_empty_missing_entity_is_404(client: TestClient) -> None:
    artist = make_artist(client)
    assert get_note(client, "artist", artist["id"]) == {
        "entity_type": "artist",
        "entity_id": artist["id"],
        "body": "",
        "updated_at": None,
        "links": [],
    }
    missing = str(uuid.uuid4())
    assert client.get(f"/notes/artist/{missing}").status_code == 404
    response = client.put(f"/notes/artist/{missing}", json={"body": "x", "base_updated_at": None})
    assert response.status_code == 404
    # The type matters: an artist ID is no recording.
    assert client.get(f"/notes/recording/{artist['id']}").status_code == 404


def test_save_read_and_delete_by_saving_empty(client: TestClient) -> None:
    artist = make_artist(client, mbid=MBID)
    saved = put_note(client, "artist", artist["id"], "# Nina\n\nHigh priestess of soul.")
    assert saved["body"] == "# Nina\n\nHigh priestess of soul."
    assert saved["entity_id"] == artist["id"]
    assert saved["updated_at"] is not None
    # Found by MBID as well as by ID.
    assert get_note(client, "artist", MBID) == saved
    # A blank body deletes the note.
    cleared = put_note(client, "artist", artist["id"], "  \n", saved["updated_at"])
    assert (cleared["body"], cleared["updated_at"]) == ("", None)
    assert get_note(client, "artist", artist["id"])["updated_at"] is None


def test_links_resolve_by_mbid_or_id_with_chips(client: TestClient) -> None:
    nina = make_artist(client, mbid=MBID)
    album = make_release_group(client, nina, date="1965")
    song = make_recording(client, nina)
    make_track(client, album, song)
    cover = client.put(
        f"/covers/release-group/{album['id']}", files={"file": ("c.png", COVER, "image/png")}
    )
    assert cover.status_code == 200, cover.text
    missing = uuid.uuid4()
    body = (
        f"🎹 [[artist:{MBID}|Nina]] sings [[recording:{song['id']}|Sinnerman]] on "
        f"[[release_group:{album['id']}]]; gone: [[artist:{missing}|Nobody]]"
    )
    note = put_note(client, "artist", nina["id"], body)
    nina_link, song_link, album_link, broken = note["links"]
    assert nina_link["target"]["id"] == nina["id"]
    assert nina_link["target"]["mbid"] == MBID
    assert (nina_link["display_text"], nina_link["ref"]) == ("Nina", MBID)
    # Ranges in UTF-16 code units: the emoji counts twice.
    assert nina_link["range"] == {"start": 3, "end": 3 + len(nina_link["raw"])}
    assert song_link["target"] == {
        "type": "recording",
        "id": song["id"],
        "mbid": None,
        "title": "Sinnerman",
        "subtitle": "Nina Simone",
        "cover_url": f"/covers/release-group/{album['id']}",
        "link_ref": song["id"],
        "link": f"[[recording:{song['id']}|Sinnerman]]",
    }
    assert album_link["display_text"] is None
    assert album_link["target"]["title"] == "Pastel Blues"
    # Broken links are kept and reported.
    assert broken["target"] is None
    assert (broken["ref"], broken["display_text"]) == (str(missing), "Nobody")
    assert get_note(client, "artist", nina["id"]) == note


def test_rebuilt_on_every_save(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    other = make_artist(client, "Other")
    write(client, "artist", nina["id"], f"[[artist:{other['id']}|O]] [[artist:{nina['id']}]]")
    assert len(session.exec(select(NoteLink)).all()) == 2
    write(client, "artist", nina["id"], f"just [[artist:{other['id']}|O]]")
    [link] = session.exec(select(NoteLink)).all()
    assert (str(link.target_id), link.raw) == (other["id"], f"[[artist:{other['id']}|O]]")
    write(client, "artist", nina["id"], "")
    assert session.exec(select(NoteLink)).all() == []


def test_link_survives_target_gaining_an_mbid_and_target_created_later(
    client: TestClient, session: Session
) -> None:
    nina = make_artist(client)
    author = make_artist(client, "Author")
    later_mbid = uuid.uuid4()
    write(
        client,
        "artist",
        author["id"],
        f"[[artist:{nina['id']}|Nina]] and [[artist:{later_mbid}|Later]]",
    )
    [first, second] = get_note(client, "artist", author["id"])["links"]
    assert first["target"]["id"] == nina["id"] and second["target"] is None

    # Nina gains an MBID: the link with her internal ID still resolves (text untouched),
    # and its chip offers the MBID for new links.
    assert client.patch(f"/artists/{nina['id']}", json={"mbid": MBID}).status_code == 200
    # The artist the MBID link names is created after the note was written.
    later = make_artist(client, "Later", mbid=str(later_mbid))
    note = get_note(client, "artist", author["id"])
    assert note["body"] == f"[[artist:{nina['id']}|Nina]] and [[artist:{later_mbid}|Later]]"
    first, second = note["links"]
    assert (first["target"]["id"], first["target"]["link_ref"]) == (nina["id"], MBID)
    assert second["target"]["id"] == later["id"]
    # Both are backlinks, by ID and by MBID.
    for target in nina, later:
        [backlink] = client.get(f"/backlinks/artist/{target['id']}").json()
        assert backlink["source"]["id"] == author["id"]
    assert client.get(f"/backlinks/artist/{MBID}").json()[0]["source"]["id"] == author["id"]


def test_link_follows_the_saved_target_if_its_mbid_changes(client: TestClient) -> None:
    nina = make_artist(client, mbid=MBID)
    author = make_artist(client, "Author")
    write(client, "artist", author["id"], f"[[artist:{MBID}|Nina]]")
    assert client.patch(f"/artists/{nina['id']}", json={"mbid": None}).status_code == 200
    [link] = get_note(client, "artist", author["id"])["links"]
    assert link["target"]["id"] == nina["id"]


def test_deleting_a_target_breaks_the_link_and_keeps_the_text(
    client: TestClient, session: Session
) -> None:
    nina = make_artist(client)
    song = make_recording(client, nina)
    body = f"Loved [[recording:{song['id']}|Sinnerman]] live."
    write(client, "artist", nina["id"], body)
    assert client.delete(f"/recordings/{song['id']}").status_code == 204
    note = get_note(client, "artist", nina["id"])
    assert note["body"] == body
    [link] = note["links"]
    assert link["target"] is None and link["display_text"] == "Sinnerman"
    [row] = session.exec(select(NoteLink)).all()
    assert row.target_id is None


def test_track_notes_are_recording_notes(client: TestClient) -> None:
    """The client shows a track's note through its recording: the same note on every
    release group (and source) it is on."""
    nina = make_artist(client)
    song = make_recording(client, nina)
    for title in "Pastel Blues", "The Best Of":
        make_track(client, make_release_group(client, nina, title), song)
    write(client, "recording", song["id"], "Every time.")
    tracks = client.get(f"/recordings/{song['id']}").json()["tracks"]
    assert len(tracks) == 2
    assert get_note(client, "recording", song["id"])["body"] == "Every time."


def test_radio_notes(client: TestClient, session: Session) -> None:
    show, episode = radio(session)
    note = write(client, "radio_episode", str(episode.id), f"From [[radio_show:{show.id}]]")
    [link] = note["links"]
    assert link["target"]["title"] == "Early Bird Show"
    assert link["target"]["subtitle"] == "NTS"
    [backlink] = client.get(f"/backlinks/radio_show/{show.id}").json()
    assert backlink["source"]["title"] == "Early Bird Show w/ Pam"
    assert backlink["source"]["subtitle"] == "Early Bird Show, 2026-11-12"
    assert backlink["snippets"][0]["text"] == "From Early Bird Show"


def test_stale_save_is_a_conflict_with_the_current_note(client: TestClient) -> None:
    nina = make_artist(client)
    path = f"/notes/artist/{nina['id']}"
    first = put_note(client, "artist", nina["id"], "one")
    # Client A saves on top of "one".
    second = put_note(client, "artist", nina["id"], "one, two", first["updated_at"])
    # Client B still has "one".
    response = client.put(path, json={"body": "one, three", "base_updated_at": first["updated_at"]})
    assert response.status_code == 409
    conflict = response.json()
    assert conflict["current"] == second
    assert conflict["detail"]
    assert get_note(client, "artist", nina["id"])["body"] == "one, two"
    # Client B thought there was no note.
    response = client.put(path, json={"body": "new", "base_updated_at": None})
    assert response.status_code == 409
    # Saving what is there anyway is no conflict.
    same = client.put(path, json={"body": "one, two", "base_updated_at": None})
    assert same.status_code == 200 and same.json() == second
    # A note deleted by another client: an edit of the old one conflicts.
    put_note(client, "artist", nina["id"], "", second["updated_at"])
    response = client.put(path, json={"body": "x", "base_updated_at": second["updated_at"]})
    assert response.status_code == 409
    assert response.json()["current"]["updated_at"] is None
    # A timestamp without a time zone is not accepted.
    response = client.put(path, json={"body": "x", "base_updated_at": "2026-11-15T10:00:00"})
    assert response.status_code == 422


def test_backlinks_with_snippets(client: TestClient) -> None:
    nina = make_artist(client)
    album = make_release_group(client, nina)
    song = make_recording(client, nina)
    other = make_artist(client, "Other")
    long_text = "word " * 40
    write(
        client,
        "artist",
        other["id"],
        f"# Influences\n\n- [[artist:{nina['id']}]] and [[release_group:{album['id']}|PB]]\n"
        f"{long_text}then [[artist:{nina['id']}|Nina \\| Simone]] {long_text}",
    )
    write(client, "release_group", album["id"], f"By 🎤 [[artist:{nina['id']}|her]].")
    write(client, "recording", song["id"], f"`[[artist:{nina['id']}]]` is code, not a link")

    backlinks = client.get(f"/backlinks/artist/{nina['id']}").json()
    assert [b["source"]["id"] for b in backlinks] == [album["id"], other["id"]]
    [snippet] = backlinks[0]["snippets"]
    assert snippet == {"text": "By 🎤 her.", "highlights": [{"start": 6, "end": 9}]}
    assert backlinks[0]["source"]["type"] == "release_group"
    first, second = backlinks[1]["snippets"]
    # Links as their display text; this entity's link without one as its title.
    assert first["text"] == "- Nina Simone and PB"
    assert first["highlights"] == [{"start": 2, "end": 13}]
    # A long line is cut around the link.
    assert second["text"].startswith("…") and second["text"].endswith("…")
    highlight = second["highlights"][0]
    assert second["text"][highlight["start"] : highlight["end"]] == "Nina | Simone"
    # Exactly the target: a link to the release group is not a backlink of its artist
    # (and the other way round).
    [album_backlink] = client.get(f"/backlinks/release_group/{album['id']}").json()
    assert album_backlink["snippets"][0]["text"] == "- Nina Simone and PB"
    assert client.get(f"/backlinks/recording/{song['id']}").json() == []
    assert client.get(f"/backlinks/artist/{uuid.uuid4()}").status_code == 404


def test_note_search(client: TestClient, session: Session) -> None:
    nina = make_artist(client)
    bjork = make_artist(client, "Björk")
    album = make_release_group(client, nina)
    show, _ = radio(session)
    write(
        client, "artist", nina["id"], "Saw her at the Village Gate.\n\nThe piano was out of tune."
    )
    write(
        client,
        "release_group",
        album["id"],
        f"Recorded live; [[artist:{bjork['id']}|Björk]] loves it",
    )
    write(client, "radio_show", str(show.id), "Great for early mornings, lots of piano")

    def search(q: str, **params: Any) -> list[dict[str, Any]]:
        response = client.get("/notes/search", params={"q": q, **params})
        assert response.status_code == 200, response.text
        return response.json()

    hits = search("piano")
    assert {hit["source"]["id"] for hit in hits} == {nina["id"], str(show.id)}
    nina_hit = next(hit for hit in hits if hit["source"]["id"] == nina["id"])
    snippet = nina_hit["snippet"]
    assert "\n" not in snippet["text"]
    [highlight] = snippet["highlights"]
    assert snippet["text"][highlight["start"] : highlight["end"]] == "piano"
    # Accent- and case-insensitive; links count as their display text, not their UUID.
    [hit] = search("BJORK")
    assert hit["source"] == {**hit["source"], "id": album["id"], "type": "release_group"}
    text = hit["snippet"]["text"]
    assert "Björk" in text and "[[" not in text
    assert search(bjork["id"]) == []
    # Web search syntax: phrases, exclusion.
    assert [h["source"]["id"] for h in search('"village gate"')] == [nina["id"]]
    assert [h["source"]["id"] for h in search("piano -tune")] == [str(show.id)]
    assert [h["source"]["id"] for h in search("piano", types="radio_show")] == [str(show.id)]
    assert search("nothing-like-this") == []


def test_deleting_entities_deletes_their_notes_and_tags_and_breaks_links(
    client: TestClient, session: Session
) -> None:
    nina = make_artist(client)
    author = make_artist(client, "Author")
    album = make_release_group(client, nina)
    song = make_recording(client, nina)
    loner = make_artist(client, "Loner")
    targets = [("release_group", album), ("recording", song), ("artist", loner)]
    body = " ".join(f"[[{type}:{entity['id']}|{type}]]" for type, entity in targets)
    write(client, "artist", author["id"], body)
    for type, entity in targets:
        write(client, type, entity["id"], f"about {type}")
        response = client.post(f"/entity-tags/{type}/{entity['id']}", json={"name": type})
        assert response.status_code == 200
        assert client.get(f"/backlinks/{type}/{entity['id']}").json()

    paths = {"release_group": "release-groups", "recording": "recordings", "artist": "artists"}
    for type, entity in targets:
        assert client.delete(f"/{paths[type]}/{entity['id']}").status_code == 204
    assert session.exec(select(Note.entity_id)).all() == [uuid.UUID(author["id"])]
    assert session.exec(select(EntityTag)).all() == []
    note = get_note(client, "artist", author["id"])
    assert note["body"] == body
    assert [link["target"] for link in note["links"]] == [None, None, None]
    assert all(link.target_id is None for link in session.exec(select(NoteLink)))
