"""yt-dlp: URLs, metadata from saved output, and a real (local, file://) download."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ep.metadata.ytdlp import Progress, YouTubeKind, YtDlp, YtDlpError, youtube_url
from ep.probe import probe
from tests.fakes import (
    CHAPTERED_VIDEO,
    PLAYLIST,
    PLAYLIST_CANONICAL,
    SINGLE_VIDEO,
    YTDLP,
    video_fixture,
)

VIDEO = SINGLE_VIDEO


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (VIDEO, VIDEO),
        ("https://youtu.be/GSWtPDAN3PE?si=abc&t=42", VIDEO),
        ("https://m.youtube.com/watch?v=GSWtPDAN3PE&feature=share", VIDEO),
        ("https://music.youtube.com/watch?v=GSWtPDAN3PE", VIDEO),
        ("http://youtube.com/shorts/GSWtPDAN3PE", VIDEO),
        ("https://www.youtube-nocookie.com/embed/GSWtPDAN3PE", VIDEO),
        (PLAYLIST, PLAYLIST_CANONICAL),
        ("https://www.youtube.com/playlist?list=PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW&si=x",
         PLAYLIST_CANONICAL),
        # A mix is generated per viewer: the video.
        ("https://www.youtube.com/watch?v=GSWtPDAN3PE&list=RDGSWtPDAN3PE", VIDEO),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/@channel", None),
        ("https://vimeo.com/123", None),
        ("youtube.com/watch?v=GSWtPDAN3PE", None),
    ],
)  # fmt: skip
def test_youtube_url(url: str, expected: str | None) -> None:
    parsed = youtube_url(url)
    assert (parsed.url if parsed else None) == expected


def test_playlist_kind() -> None:
    parsed = youtube_url(PLAYLIST)
    assert parsed is not None and parsed.kind is YouTubeKind.playlist


def test_video_metadata() -> None:
    info = video_fixture("GSWtPDAN3PE")
    assert info.title == "aedis - salt echoes [d2e006]"
    assert info.channel == "down2earth"
    assert info.artists == [] and info.track is None
    assert info.duration_ms == 409000
    assert not info.chapters
    assert info.best_thumbnail is not None and info.best_thumbnail.startswith("https://i.ytimg")

    topic = video_fixture("-i8dwSGgZjE")
    assert (topic.artists, topic.track, topic.album) == (
        ["Ernest Hood"],
        "Saturday Morning Doze",
        "Neighborhoods",
    )

    chaptered = video_fixture("Hx5aGvXCwyQ")
    assert chaptered.chapters is not None and len(chaptered.chapters) == 9
    assert chaptered.chapters[-1].title == "The Festival (Tale)"


def fake_command(tmp_path: Path, stdout: str, stderr: str = "", code: int = 0) -> list[str]:
    """A stand-in yt-dlp executable printing this output."""
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n"
    )
    return [sys.executable, str(script)]


def test_metadata_through_the_command(tmp_path: Path) -> None:
    data = (YTDLP / "playlist_PLpCe82icWpaIvy78b4vyPWy3h8WThV5pW.json").read_text()
    playlist = YtDlp(fake_command(tmp_path, data)).playlist(PLAYLIST_CANONICAL)
    assert playlist.title == "Ernest Hood - Neighborhoods (Full Album)"
    assert len(playlist.entries) == 9
    assert playlist.entries[2].title == "Unreleased"

    video = json.loads((YTDLP / "video_GSWtPDAN3PE.json").read_text())
    with pytest.raises(YtDlpError, match="is not a playlist"):
        YtDlp(fake_command(tmp_path, json.dumps(video))).playlist(VIDEO)


def test_errors_carry_the_ytdlp_message(tmp_path: Path) -> None:
    stderr = "WARNING: something\nERROR: [youtube] GSWtPDAN3PE: Private video. Sign in\n"
    ytdlp = YtDlp(fake_command(tmp_path, "", stderr, code=1))
    with pytest.raises(YtDlpError, match=r"^\[youtube\] GSWtPDAN3PE: Private video. Sign in$"):
        ytdlp.video(VIDEO)


def test_download_keeps_the_codec_and_reports_progress(tmp_path: Path) -> None:
    """A real yt-dlp run on a local file (``--enable-file-urls``): no network."""
    source = tmp_path / "source" / "song.webm"
    source.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:a", "libopus", str(source)],
        check=True,
    )  # fmt: skip
    out = tmp_path / "out"
    out.mkdir()
    updates: list[Progress] = []
    ytdlp = YtDlp(extra_args=["--enable-file-urls"])
    path = ytdlp.download(source.as_uri(), out, updates.append)
    assert path.parent == out and path.suffix == ".opus"
    assert probe(path).codec == "opus"  # remuxed from WebM, not re-encoded
    assert updates and updates[-1].downloaded_bytes == updates[-1].total_bytes
    assert [p.name for p in out.iterdir()] == [path.name]

    with pytest.raises(YtDlpError):
        ytdlp.download((tmp_path / "missing.webm").as_uri(), out)


@pytest.mark.network
def test_network_metadata() -> None:
    ytdlp = YtDlp()
    video = ytdlp.video(VIDEO)
    assert video.title == "aedis - salt echoes [d2e006]"
    playlist = ytdlp.playlist(PLAYLIST_CANONICAL)
    assert len(playlist.entries) >= 8
    chaptered = ytdlp.video(CHAPTERED_VIDEO)
    assert chaptered.chapters and len(chaptered.chapters) == 9
