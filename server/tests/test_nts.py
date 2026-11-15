"""NTS API client: URLs, parsing the saved episode and show, shape checks, the live API."""

import json
import uuid

import httpx2
import pytest

from ep.importers.download import is_downloadable
from ep.importers.nts import time_ranges
from ep.metadata.nts import (
    EpisodeRef,
    Nts,
    NtsNotFound,
    NtsUnexpectedResponse,
    NtsUnreachable,
    episode_ref,
    parse_episode,
    parse_show,
    soundcloud_url,
)
from tests.fakes import NTS, NTS_EPISODE, NTS_EPISODE_API, NTS_SOUNDCLOUD, nts_transport

EPISODE_FILE = NTS / "episode_early-bird-show-w-pam-12th-november-2026.json"
REF = EpisodeRef("early-bird-show-w-pam", "early-bird-show-w-pam-12th-november-2026")


def test_episode_urls() -> None:
    for url in (
        NTS_EPISODE,
        NTS_EPISODE + "/",
        NTS_EPISODE.replace("www.", "") + "?utm_source=x#top",
        NTS_EPISODE.replace("https", "http"),
    ):
        assert episode_ref(url) == REF, url
    assert REF.url == NTS_EPISODE
    for url in (
        "https://www.nts.live/shows/early-bird-show-w-pam",
        "https://www.nts.live/",
        "https://example.com/shows/a/episodes/b",
        "ftp://www.nts.live/shows/a/episodes/b",
        "not a url",
    ):
        assert episode_ref(url) is None, url


def test_soundcloud_urls() -> None:
    raw = NTS_SOUNDCLOUD + "?utm_medium=api&utm_campaign=social_sharing&utm_source=id_314736"
    assert soundcloud_url(raw) == NTS_SOUNDCLOUD
    assert soundcloud_url("https://m.soundcloud.com/a/b/") == "https://soundcloud.com/a/b"
    for url in ("https://soundcloud.com/a", "https://soundcloud.com/a/sets", "https://x.com/a/b"):
        assert soundcloud_url(url) is None
    # Only canonical URLs of known hosts reach yt-dlp.
    assert is_downloadable(NTS_SOUNDCLOUD)
    assert is_downloadable(NTS_EPISODE)
    assert not is_downloadable(raw)
    assert not is_downloadable("https://example.com/x")


def test_parse_episode() -> None:
    episode = parse_episode(EPISODE_FILE.read_bytes())
    assert episode.name == "The Early Bird Show w/ PAM & Alice"
    assert (episode.show_alias, episode.episode_alias) == (REF.show, REF.episode)
    assert episode.broadcast is not None and episode.broadcast.isoformat() == (
        "2026-11-12T07:00:00+00:00"
    )
    assert episode.location_long == "Paris"
    assert [g.value for g in episode.genres] == ["Folk", "Ambient", "Indie Rock", "Dream Pop"]
    assert episode.moods == []
    assert episode.soundcloud_url == NTS_SOUNDCLOUD
    assert episode.media.image_url and episode.media.image_url.startswith("https://media2.")
    tracklist = episode.tracklist
    assert len(tracklist) == 32

    # Typed in by hand: estimated offset and duration, no ids.
    first = tracklist[0]
    assert (first.artist, first.title) == ("Nigel Peake", "NORTH SIX PARTS")
    assert first.estimate
    assert (first.start_ms, first.duration_ms, first.detected_duration_ms) == (8000, 422000, None)
    assert first.isrc_id is first.deezer_track_id is first.musicbrainz_track_id is None

    # Detected: exact offset and duration, ISRC, Deezer, ACRCloud; no MBID.
    second = tracklist[1]
    assert not second.estimate
    assert (second.start_ms, second.duration_ms, second.detected_duration_ms) == (
        430000,
        22000,
        22000,
    )
    assert (second.isrc_id, second.deezer_track_id) == ("NLGJ30900913", 12100263)
    assert second.acr_id == "9e81f635ae6464f83baec4562896095a"
    assert second.musicbrainz_track_id is None

    # With a MusicBrainz recording MBID.
    kozelek = tracklist[4]
    assert (kozelek.artist, kozelek.title) == ("Mark Kozelek", "Ruth Marie")
    assert kozelek.musicbrainz_track_id == uuid.UUID("15c4306c-0eec-424c-a5c0-a506bec52320")
    assert kozelek.isrc_id == "US36Y0400101"
    assert sum(t.estimate for t in tracklist) == 5
    assert sum(t.musicbrainz_track_id is not None for t in tracklist) == 12


def test_time_ranges() -> None:
    tracklist = parse_episode(EPISODE_FILE.read_bytes()).tracklist
    ranges = time_ranges(tracklist)
    assert ranges[0] == (8000, 430000)
    assert ranges[1] == (430000, 452000)
    assert ranges[-1] == (7148000, 7285000)
    # No duration: up to the next entry; no offset at all: no range.
    data = json.loads(EPISODE_FILE.read_bytes())
    entries = data["embeds"]["tracklist"]["results"]
    entries[0]["duration_estimate"] = None
    entries[2]["offset_estimate"] = entries[2]["duration_estimate"] = None
    tracklist = parse_episode(json.dumps(data).encode()).tracklist
    ranges = time_ranges(tracklist)
    assert ranges[0] == (8000, 430000)
    assert ranges[2] == (None, None)


def test_parse_show() -> None:
    show = parse_show((NTS / "show_early-bird-show-w-pam.json").read_bytes())
    assert show.name == "The Early Bird Show w/ PAM"
    assert show.show_alias == "early-bird-show-w-pam"
    assert show.description and show.description.startswith("PAM, previously host")
    assert show.media.image_url


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("embeds"), "embeds: Field required"),
        (lambda d: d["embeds"]["tracklist"]["results"][3].pop("offset"), "offset: Field required"),
        (
            lambda d: d["embeds"]["tracklist"]["results"][0].update(artist=None),
            "artist: Input should be a valid string",
        ),
        (lambda d: d.update(broadcast="yesterday"), "broadcast: Input should be a valid datetime"),
        (
            lambda d: d["embeds"]["tracklist"]["metadata"]["resultset"].update(count=40),
            "the embedded tracklist has 32 of 40 entries",
        ),
    ],
)
def test_shape_changes_fail_loudly(change, message: str) -> None:
    data = json.loads(EPISODE_FILE.read_bytes())
    change(data)
    with pytest.raises(NtsUnexpectedResponse, match=message):
        parse_episode(json.dumps(data).encode())


def test_client_errors() -> None:
    with Nts(transport=nts_transport()) as nts:
        assert nts.get_episode(REF).name.startswith("The Early Bird Show")
        with pytest.raises(NtsNotFound):
            nts.get_episode(EpisodeRef("early-bird-show-w-pam", "gone"))
    blocked = {NTS_EPISODE_API: httpx2.Response(403, content=b"<html>challenge</html>")}
    with Nts(transport=nts_transport(responses=blocked)) as nts, pytest.raises(NtsUnreachable):
        nts.get_episode(REF)
    html = {NTS_EPISODE_API: httpx2.Response(200, content=b"<html>maintenance</html>")}
    with (
        Nts(transport=nts_transport(responses=html)) as nts,
        pytest.raises(NtsUnexpectedResponse, match="unexpected NTS episode response"),
    ):
        nts.get_episode(REF)


def test_user_agent_is_generic() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=EPISODE_FILE.read_bytes())

    with Nts(transport=httpx2.MockTransport(handle)) as nts:
        nts.get_episode(REF)
    agent = seen[0].headers["user-agent"]
    assert agent.startswith("EP/") and "@" not in agent and "(" not in agent


@pytest.mark.network
def test_live_episode() -> None:
    """NTS's API still answers in the shape EP knows, for the fixture episode."""
    with Nts() as nts:
        episode = nts.get_episode(REF)
        show = nts.get_show(REF.show)
    assert episode.name == "The Early Bird Show w/ PAM & Alice"
    assert len(episode.tracklist) == 32
    assert episode.soundcloud_url == NTS_SOUNDCLOUD
    assert show.name == "The Early Bird Show w/ PAM"
