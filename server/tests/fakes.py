"""Stand-ins for yt-dlp and the web, for Bandcamp and YouTube import tests.

- ``FakeYtDlp`` answers metadata from saved yt-dlp output in ``fixtures/ytdlp``
  (``video_<id>.json``, ``playlist_<id>.json``, real responses with formats, captions
  and heatmaps left out) and "downloads" by generating a short silent file (the
  importers take lengths from the metadata, not from the file);
- ``web_transport`` serves the Bandcamp pages in ``fixtures/bandcamp`` and a PNG for any
  image URL (covers).

The fixtures:

- ``video_GSWtPDAN3PE``: "aedis - salt echoes [d2e006]" (down2earth; no music fields,
  no chapters; not in MusicBrainz)
- ``video_Hx5aGvXCwyQ``: "Tales Under The Oak - The Toad Folk II (full album) (...)",
  9 chapters
- ``playlist_PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW`` (``--flat-playlist``): "Ernest Hood -
  Neighborhoods (Full Album)", 9 entries: the 8 album tracks ("Ernest Hood - Topic"
  videos with artist/track/album fields) and, as entry 3, "Unreleased" (another
  channel, 58 minutes), each in ``video_<id>.json``
- ``bandcamp/knotwilg_leitmotiv-2.html``: Maxine Funke, "Leitmotiv" on the Knotwilg
  label's page (5 tracks; not in MusicBrainz)
- ``bandcamp/floatingpoints_promises.html``: Floating Points, Pharoah Sanders & The
  London Symphony Orchestra, "Promises" on the artist's page (9 tracks; linked from
  MusicBrainz, see ``tests.mock_musicbrainz``)
- ``nts/episode_early-bird-show-w-pam-12th-november-2026.json``: NTS's API response for
  "The Early Bird Show w/ PAM & Alice" of 2026-11-12 (32 tracklist entries: 27 with
  detected times, ISRCs, Deezer and ACRCloud ids, 12 of them with a MusicBrainz
  recording MBID; 5 typed in with estimated times; audio on SoundCloud), and
  ``nts/show_early-bird-show-w-pam.json``, its show; served by ``nts_transport``
"""

import atexit
import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx2

from ep.metadata.ytdlp import PlaylistInfo, Progress, VideoInfo, YtDlp, YtDlpError, youtube_url

FIXTURES = Path(__file__).parent / "fixtures"
YTDLP = FIXTURES / "ytdlp"
BANDCAMP = FIXTURES / "bandcamp"
NTS = FIXTURES / "nts"

SINGLE_VIDEO = "https://www.youtube.com/watch?v=GSWtPDAN3PE"
CHAPTERED_VIDEO = "https://www.youtube.com/watch?v=Hx5aGvXCwyQ"
PLAYLIST = "https://www.youtube.com/watch?v=-i8dwSGgZjE&list=PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW"
PLAYLIST_CANONICAL = "https://www.youtube.com/playlist?list=PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW"
UNRELEASED = "https://www.youtube.com/watch?v=J0vUqvfHQeQ"
KNOTWILG = "https://knotwilg.bandcamp.com/album/leitmotiv-2"
PROMISES = "https://floatingpoints.bandcamp.com/album/promises"
NTS_EPISODE = (
    "https://www.nts.live/shows/early-bird-show-w-pam/episodes/"
    "early-bird-show-w-pam-12th-november-2026"
)
NTS_SOUNDCLOUD = "https://soundcloud.com/user-202286394-991268468/the-early-bird-show-516135418"
NTS_EPISODE_API = (
    "https://www.nts.live/api/v2/shows/early-bird-show-w-pam/episodes/"
    "early-bird-show-w-pam-12th-november-2026"
)
NTS_API = {
    NTS_EPISODE_API: "episode_early-bird-show-w-pam-12th-november-2026.json",
    "https://www.nts.live/api/v2/shows/early-bird-show-w-pam": "show_early-bird-show-w-pam.json",
}
BANDCAMP_PAGES = {
    KNOTWILG: "knotwilg_leitmotiv-2.html",
    PROMISES: "floatingpoints_promises.html",
}


def video_fixture(video_id: str) -> VideoInfo:
    return VideoInfo.model_validate_json((YTDLP / f"video_{video_id}.json").read_bytes())


_GENERATED: dict[tuple[str, ...], Path] = {}


def ffmpeg_output(path: Path, *args: str | Path) -> Path:
    """Write ``ffmpeg <args> <path>`` to ``path``. Each distinct command (input files
    by content) runs once per test session, into a private temp dir; later calls copy
    its output."""
    key = (
        *(hashlib.sha256(a.read_bytes()).hexdigest() if isinstance(a, Path) else a for a in args),
        path.suffix,
    )
    if (made := _GENERATED.get(key)) is None:
        made = Path(tempfile.mkdtemp(prefix="ep-test-audio-")) / f"audio{path.suffix}"
        atexit.register(shutil.rmtree, made.parent, ignore_errors=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args), str(made)], check=True)
        _GENERATED[key] = made
    shutil.copyfile(made, path)
    return path


def silent_audio(path: Path, seconds: float = 1.0) -> Path:
    return ffmpeg_output(path, "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:d={seconds}")


class FakeYtDlp(YtDlp):
    """Metadata from the fixtures; downloads are generated files.

    ``videos`` adds or replaces videos by URL; ``unavailable`` URLs fail like a removed
    video; ``failing_downloads`` fail to download (until removed from the set).
    """

    def __init__(self) -> None:
        super().__init__()
        self.videos: dict[str, VideoInfo] = {}
        self.unavailable: set[str] = set()
        self.failing_downloads: set[str] = set()
        self.downloads: list[str] = []
        self.metadata_calls: list[str] = []

    def video(self, url: str) -> VideoInfo:
        self.metadata_calls.append(url)
        if url in self.unavailable:
            raise YtDlpError("[youtube] Video unavailable. This video has been removed")
        if url in self.videos:
            return self.videos[url]
        parsed = youtube_url(url)
        path = YTDLP / f"video_{parsed.id if parsed else url}.json"
        if parsed is None or not path.exists():
            raise YtDlpError(f"[generic] no fixture for {url}")
        return VideoInfo.model_validate_json(path.read_bytes())

    def playlist(self, url: str) -> PlaylistInfo:
        self.metadata_calls.append(url)
        parsed = youtube_url(url)
        path = YTDLP / f"playlist_{parsed.id if parsed else url}.json"
        if parsed is None or not path.exists():
            raise YtDlpError(f"[youtube:tab] no fixture for {url}")
        return PlaylistInfo.model_validate_json(path.read_bytes())

    def download(
        self,
        url: str,
        directory: Path,
        progress: Callable[[Progress], None] | None = None,
        timeout: float = 0,
    ) -> Path:
        self.downloads.append(url)
        if url in self.failing_downloads:
            raise YtDlpError(
                "[youtube] Video unavailable. The uploader has not made this video "
                "available in your country"
            )
        parsed = youtube_url(url)
        name = parsed.id if parsed else urlsplit(url).path.rsplit("/", 1)[-1]
        extension = ".mp3" if "bandcamp.com" in url else ".opus"
        if progress is not None:
            progress(Progress(500, 1000))
            progress(Progress(1000, 1000))
        return silent_audio(directory / f"{name}{extension}")


def png_bytes() -> bytes:
    return (FIXTURES / "cover.png").read_bytes()


def web_transport(requests: list[str] | None = None) -> httpx2.MockTransport:
    """Bandcamp pages from the fixtures (404 for others) and a PNG for image URLs
    (Bandcamp, YouTube, Cover Art Archive)."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if requests is not None:
            requests.append(url)
        if url in BANDCAMP_PAGES:
            return httpx2.Response(200, content=(BANDCAMP / BANDCAMP_PAGES[url]).read_bytes())
        host = request.url.host
        if host.endswith(("bcbits.com", "ytimg.com", "coverartarchive.org")):
            return httpx2.Response(200, content=png_bytes())
        return httpx2.Response(404, content=b"not found")

    return httpx2.MockTransport(handle)


def nts_transport(
    requests: list[str] | None = None, responses: dict[str, httpx2.Response] | None = None
) -> httpx2.MockTransport:
    """NTS's API from the fixtures (404 for other URLs, ``responses`` override by URL)
    and a PNG for NTS's images."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if requests is not None:
            requests.append(url)
        if responses and url in responses:
            return responses[url]
        if url in NTS_API:
            return httpx2.Response(200, content=(NTS / NTS_API[url]).read_bytes())
        if request.url.host.endswith("ntslive.co.uk"):
            return httpx2.Response(200, content=png_bytes())
        return httpx2.Response(404, content=b"not found")

    return httpx2.MockTransport(handle)
