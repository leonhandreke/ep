"""yt-dlp as a subprocess: metadata (``--dump-single-json``) and audio downloads (``-x``).

yt-dlp runs as ``python -m yt_dlp`` of this environment (the ``yt-dlp[default,deno]``
dependency: the EJS challenge scripts and Deno, the JavaScript runtime YouTube needs),
with ``--ignore-config`` so that no user or system configuration changes what it does.

- ``YtDlp.video``: one video's metadata (title, uploader/channel, artist/track/album
  where YouTube Music or the extractor provides them, duration, chapters, thumbnails);
- ``YtDlp.playlist``: a playlist's entries (``--flat-playlist``: one request, entries
  with title, duration and URL only; fetch each entry with ``video`` for the rest);
- ``YtDlp.download``: the best audio stream with ``-x`` and no ``--audio-format``: the
  original codec is kept (opus, m4a/aac, mp3), only the container changes. Partial
  downloads (``.part``) in the directory are resumed. Progress goes to a callback.

``youtube_url`` canonicalises YouTube URLs (youtu.be, shorts, music.youtube.com,
tracking parameters) to ``https://www.youtube.com/watch?v=<id>`` or
``https://www.youtube.com/playlist?list=<id>``, so that a link is one ``source`` row.
"""

import json
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Seconds a metadata call may take (a playlist of a few hundred entries takes ~10 s).
METADATA_TIMEOUT = 300
# Seconds a download may take.
DOWNLOAD_TIMEOUT = 3 * 3600

_PROGRESS = "[progress]"
_FILE = "[file]"


class YtDlpError(Exception):
    """yt-dlp failed; the message is its ``ERROR:`` line (video unavailable, private,
    blocked in this country, ...)."""


# Metadata. Field names follow yt-dlp's info dict; unknown fields are ignored.


class YtModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Chapter(YtModel):
    # Seconds.
    start_time: float
    end_time: float | None = None
    title: str = ""


class Thumbnail(YtModel):
    url: str
    width: int | None = None
    height: int | None = None
    # Higher is better (yt-dlp's own ranking).
    preference: int | None = None


class VideoInfo(YtModel):
    id: str
    title: str
    # The canonical page URL.
    webpage_url: str
    extractor_key: str = ""
    uploader: str | None = None
    channel: str | None = None
    # Music metadata (YouTube Music "Topic" videos, Bandcamp): often absent.
    artists: list[str] = Field(default_factory=list)
    track: str | None = None
    track_number: int | None = None
    album: str | None = None
    album_artists: list[str] = Field(default_factory=list)
    release_year: int | None = None
    # YYYYMMDD.
    release_date: str | None = None
    # Seconds.
    duration: float | None = None
    chapters: list[Chapter] | None = None
    thumbnail: str | None = None
    thumbnails: list[Thumbnail] = Field(default_factory=list)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int | None:
        return round(self.duration * 1000) if self.duration else None

    @property
    def best_thumbnail(self) -> str | None:
        """The largest thumbnail (yt-dlp sorts them worst first)."""
        sized = [t for t in self.thumbnails if t.width and t.height]
        if sized:
            return max(sized, key=lambda t: (t.width or 0) * (t.height or 0)).url
        return self.thumbnail


class PlaylistEntry(YtModel):
    """A playlist entry from ``--flat-playlist``: what the playlist page shows."""

    id: str
    url: str
    title: str | None = None
    duration: float | None = None
    uploader: str | None = None
    channel: str | None = None


class PlaylistInfo(YtModel):
    id: str
    title: str
    webpage_url: str
    uploader: str | None = None
    channel: str | None = None
    description: str | None = None
    thumbnails: list[Thumbnail] = Field(default_factory=list)
    entries: list[PlaylistEntry] = Field(default_factory=list)


# URLs


class YouTubeKind(StrEnum):
    video = "video"
    playlist = "playlist"


@dataclass(frozen=True)
class YouTubeUrl:
    kind: YouTubeKind
    id: str

    @property
    def url(self) -> str:
        if self.kind is YouTubeKind.video:
            return f"https://www.youtube.com/watch?v={self.id}"
        return f"https://www.youtube.com/playlist?list={self.id}"


_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LIST_ID = re.compile(r"^[A-Za-z0-9_-]{2,}$")
_VIDEO_PATHS = re.compile(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})/?$")


def _playlist_id(value: str | None) -> str | None:
    # Mixes ("RD...") are generated per viewer, "WL"/"LL"/"LM" are private: not albums.
    private = {"WL", "LL", "LM"}
    if value and _LIST_ID.match(value) and not value.startswith("RD") and value not in private:
        return value
    return None


def youtube_url(url: str) -> YouTubeUrl | None:
    """The video or playlist a YouTube URL points at, or None if it is not one.

    A watch URL with a playlist (``watch?v=...&list=...``) is the playlist: that is what
    an album link copied from a playlist looks like. Other parameters (``si``, ``t``,
    ``feature``, ``index``, ...) are dropped.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.hostname is None:
        return None
    host = parts.hostname.lower().removeprefix("www.")
    query = parse_qs(parts.query)
    video: str | None = None
    if host == "youtu.be":
        video = parts.path.strip("/")
    elif host in _HOSTS:
        if parts.path.rstrip("/") == "/playlist":
            if playlist := _playlist_id(next(iter(query.get("list", [])), None)):
                return YouTubeUrl(YouTubeKind.playlist, playlist)
            return None
        if parts.path.rstrip("/") == "/watch":
            video = next(iter(query.get("v", [])), None)
        elif m := _VIDEO_PATHS.match(parts.path):
            video = m.group(1)
    else:
        return None
    if playlist := _playlist_id(next(iter(query.get("list", [])), None)):
        return YouTubeUrl(YouTubeKind.playlist, playlist)
    if video and _VIDEO_ID.match(video):
        return YouTubeUrl(YouTubeKind.video, video)
    return None


# Running yt-dlp


@dataclass(frozen=True)
class Progress:
    downloaded_bytes: int
    total_bytes: int | None


def _error_message(output: str) -> str:
    lines = output.splitlines()
    if errors := [line.split("ERROR:", 1)[1].strip() for line in lines if "ERROR:" in line]:
        return errors[-1]
    lines = [line for line in lines if line.strip()]
    return lines[-1].strip() if lines else "yt-dlp failed"


def _int(value: str) -> int | None:
    return int(float(value)) if re.fullmatch(r"\d+(?:\.\d+)?", value) else None


class YtDlp:
    """Runs yt-dlp. ``command`` and ``extra_args`` are for tests (e.g. file:// URLs)."""

    def __init__(
        self,
        command: Sequence[str] = (sys.executable, "-m", "yt_dlp"),
        extra_args: Sequence[str] = (),
    ) -> None:
        self.command = [*command, "--ignore-config", *extra_args]
        self._schemes = {"http", "https"} | (
            {"file"} if "--enable-file-urls" in extra_args else set()
        )

    def _check_url(self, url: str) -> str:
        """Only web URLs (yt-dlp would also take search keys like ``ytsearch:``); passed
        after ``--`` so that no URL is read as an option."""
        try:
            scheme = urlsplit(url).scheme
        except ValueError:
            scheme = ""
        if scheme not in self._schemes:
            raise YtDlpError(f"not a web URL: {url!r}")
        return url

    def _json(self, *args: str) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [*self.command, "--dump-single-json", "--no-warnings", *args],
                capture_output=True,
                text=True,
                timeout=METADATA_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise YtDlpError(f"yt-dlp took longer than {METADATA_TIMEOUT} s") from e
        if result.returncode != 0:
            raise YtDlpError(_error_message(result.stderr))
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise YtDlpError(f"yt-dlp printed no JSON: {e}") from e

    def video(self, url: str) -> VideoInfo:
        data = self._json("--no-playlist", "--", self._check_url(url))
        try:
            return VideoInfo.model_validate(data)
        except ValidationError as e:
            raise YtDlpError(f"unexpected yt-dlp metadata for {url}: {e}") from e

    def playlist(self, url: str) -> PlaylistInfo:
        data = self._json("--flat-playlist", "--yes-playlist", "--", self._check_url(url))
        if data.get("_type") != "playlist":
            raise YtDlpError(f"{url} is not a playlist")
        try:
            return PlaylistInfo.model_validate(data)
        except ValidationError as e:
            raise YtDlpError(f"unexpected yt-dlp playlist metadata for {url}: {e}") from e

    def download(
        self,
        url: str,
        directory: Path,
        progress: Callable[[Progress], None] | None = None,
        timeout: float = DOWNLOAD_TIMEOUT,
    ) -> Path:
        """Download the audio of ``url`` into ``directory`` (``<id>.<ext>``, original
        codec) and return the file's path. Resumes a partial download there. yt-dlp is
        killed after ``timeout`` seconds."""
        template = (
            f"download:{_PROGRESS} %(progress.downloaded_bytes)s "
            "%(progress.total_bytes)s %(progress.total_bytes_estimate)s"
        )
        args = [
            *self.command,
            *("--extract-audio", "--format", "bestaudio/best", "--no-playlist"),
            *("--continue", "--no-mtime", "--newline", "--no-simulate", "--progress"),
            *("--progress-template", template),
            *("--print", f"after_move:{_FILE} %(filepath)s"),
            *("--paths", str(directory), "--output", "%(id)s.%(ext)s"),
            "--",
            self._check_url(url),
        ]
        output: list[str] = []
        files: list[Path] = []
        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        ) as process:
            assert process.stdout is not None
            # Reading the output blocks while yt-dlp is silent (a stalled connection): a
            # timer kills it, which ends the output.
            timed_out = threading.Event()

            def kill() -> None:
                timed_out.set()
                process.kill()

            watchdog = threading.Timer(timeout, kill)
            watchdog.start()
            try:
                for line in process.stdout:
                    line = line.rstrip("\n")
                    if line.startswith(_PROGRESS):
                        if progress is not None:
                            _report(line, progress)
                    elif line.startswith(_FILE):
                        files.append(Path(line.removeprefix(_FILE).strip()))
                    else:
                        output.append(line)
                process.wait()
            finally:
                watchdog.cancel()
                if process.poll() is None:  # an exception while reading (e.g. progress)
                    process.kill()
        if timed_out.is_set():
            raise YtDlpError(f"download took longer than {timeout} s")
        if process.returncode != 0:
            raise YtDlpError(_error_message("\n".join(output)))
        if len(files) != 1 or not files[0].is_file():
            raise YtDlpError(f"yt-dlp reported {len(files)} file(s), expected one audio file")
        return files[0]


def _report(line: str, progress: Callable[[Progress], None]) -> None:
    values = line.removeprefix(_PROGRESS).split()
    if len(values) != 3 or (downloaded := _int(values[0])) is None:
        return
    total = _int(values[1]) or _int(values[2])
    progress(Progress(downloaded, total))
