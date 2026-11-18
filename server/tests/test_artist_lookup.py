"""Finding the MusicBrainz artist of an incoming artist (``ep.importers.artist_lookup``)."""

import uuid
from collections.abc import Sequence
from typing import Any

from ep.importers.artist_lookup import find_mb_artist
from ep.metadata.musicbrainz import MBArtist, MBRecording, MBRelease
from tests.mock_musicbrainz import MockMusicBrainz

NINA = uuid.UUID("af8e4cc5-ef54-458d-a194-7b210acf638f")
OTHER_NINA = uuid.UUID("00000000-0000-4000-8000-000000000001")


def mb_artist(mbid: uuid.UUID, name: str, **fields: Any) -> dict[str, Any]:
    sort_name = fields.pop("sort_name", name)
    return {"id": str(mbid), "name": name, "sort-name": sort_name, "score": 100, **fields}


def credit(artist: dict[str, Any], name: str | None = None) -> list[dict[str, Any]]:
    return [{"name": name or artist["name"], "artist": artist}]


class SearchingMusicBrainz(MockMusicBrainz):
    """Answers searches with given hits (raw JSON shapes) and records the queries."""

    def __init__(
        self,
        releases: Sequence[dict[str, Any]] = (),
        recordings: Sequence[dict[str, Any]] = (),
        artists: Sequence[dict[str, Any]] = (),
    ) -> None:
        super().__init__()
        self.hits = {"release": releases, "recording": recordings, "artist": artists}
        self.queries: list[tuple[str, str]] = []

    def search_releases(self, query: str, limit: int = 25) -> list[MBRelease]:
        self.queries.append(("release", query))
        return [MBRelease.model_validate(hit) for hit in self.hits["release"]]

    def search_recordings(self, query: str, limit: int = 25) -> list[MBRecording]:
        self.queries.append(("recording", query))
        return [MBRecording.model_validate(hit) for hit in self.hits["recording"]]

    def search_artists(self, query: str, limit: int = 25) -> list[MBArtist]:
        self.queries.append(("artist", query))
        return [MBArtist.model_validate(hit) for hit in self.hits["artist"]]


def release(title: str, artist: dict[str, Any], score: int = 100) -> dict[str, Any]:
    hit_id = str(uuid.uuid4())
    return {"id": hit_id, "title": title, "score": score, "artist-credit": credit(artist)}


def test_release_title_picks_the_homonym() -> None:
    nina = mb_artist(NINA, "Nina Simone")
    other = mb_artist(OTHER_NINA, "Nina Simone", disambiguation="someone else")
    mb = SearchingMusicBrainz(releases=[release("Pastel Blues", nina)], artists=[nina, other])
    found = find_mb_artist(mb, "Nina Simone", release_titles=["Pastel Blues"])
    assert found is not None and found.id == NINA
    assert mb.queries == [("release", 'release:"Pastel Blues" AND artist:"Nina Simone"')]


def test_title_confirms_a_close_name() -> None:
    sigur_ros = mb_artist(NINA, "Sigur Rós")
    mb = SearchingMusicBrainz(recordings=[release("Hoppípolla", sigur_ros)])
    found = find_mb_artist(mb, "Sigur Ross", recording_titles=["Hoppipolla"])
    assert found is not None and found.id == NINA


def test_other_artists_of_the_hits_are_not_taken() -> None:
    sandy = mb_artist(OTHER_NINA, "Sandy Denny")
    mb = SearchingMusicBrainz(
        releases=[release("Quiet Village", sandy)], artists=[mb_artist(NINA, "Martin Denny")]
    )
    found = find_mb_artist(mb, "Martin Denny", release_titles=["Quiet Village"])
    # Not from the release hits; the artist search has the only Martin Denny.
    assert found is not None and found.id == NINA
    assert [kind for kind, _ in mb.queries] == ["release", "artist"]


def test_low_scored_hits_are_ignored() -> None:
    nina = mb_artist(NINA, "Nina Simone")
    mb = SearchingMusicBrainz(releases=[release("Pastel Blues", nina, score=60)])
    assert find_mb_artist(mb, "Nina Simone", release_titles=["Pastel Blues"]) is None


def test_artist_search_needs_a_single_equal_name() -> None:
    jetty = mb_artist(NINA, "Jetty")
    other = mb_artist(OTHER_NINA, "Jetty", disambiguation="another band")
    assert find_mb_artist(SearchingMusicBrainz(artists=[jetty, other]), "Jetty") is None
    found = find_mb_artist(SearchingMusicBrainz(artists=[jetty]), "Jetty")
    assert found is not None and found.id == NINA
    # A look-alike is not the artist, without a title to confirm it.
    jeanette = mb_artist(NINA, "Jeanette")
    assert find_mb_artist(SearchingMusicBrainz(artists=[jeanette]), "Jetty") is None
    close = mb_artist(NINA, "Sigur Rós")
    assert find_mb_artist(SearchingMusicBrainz(artists=[close]), "Sigur Ross") is None


def test_artist_search_by_alias_or_sort_name() -> None:
    beatles = mb_artist(NINA, "The Beatles", sort_name="Beatles, The")
    found = find_mb_artist(SearchingMusicBrainz(artists=[beatles]), "Beatles")
    assert found is not None and found.id == NINA
    hint = mb_artist(NINA, "Björk", aliases=[{"name": "Bjork Gudmundsdottir"}])
    found = find_mb_artist(SearchingMusicBrainz(artists=[hint]), "Björk Guðmundsdóttir")
    assert found is not None and found.id == NINA


def test_homonyms_on_releases_of_the_title_leave_it_open() -> None:
    nina = mb_artist(NINA, "Nina Simone")
    other = mb_artist(OTHER_NINA, "Nina Simone")
    mb = SearchingMusicBrainz(
        releases=[release("Pastel Blues", nina), release("Pastel Blues", other)], artists=[nina]
    )
    assert find_mb_artist(mb, "Nina Simone", release_titles=["Pastel Blues"]) is None
    assert [kind for kind, _ in mb.queries] == ["release"]
