"""Downloads with yt-dlp for importers (Bandcamp, YouTube): progress into the job log."""

from pathlib import Path

from ep.importers.framework import DownloadError, JobContext
from ep.metadata import bandcamp
from ep.metadata.ytdlp import Progress, YouTubeKind, YtDlp, YtDlpError, youtube_url

# Progress is logged at these fractions of a download.
PROGRESS_STEPS = (0.25, 0.5, 0.75)


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def ytdlp_download(ctx: JobContext, ytdlp: YtDlp, url: str, directory: Path) -> Path:
    """The audio of ``url`` (a canonical YouTube video or Bandcamp track URL: only
    hosts EP knows reach yt-dlp) downloaded into ``directory`` (see ``YtDlp.download``);
    ``DownloadError`` with yt-dlp's message on failure."""
    parsed = youtube_url(url)
    video = parsed.url if parsed and parsed.kind is YouTubeKind.video else None
    if url not in (video, bandcamp.canonical_url(url)):
        raise DownloadError(f"not a canonical YouTube or Bandcamp URL: {url!r}")
    steps = list(PROGRESS_STEPS)

    def progress(update: Progress) -> None:
        if not update.total_bytes:
            return
        fraction = update.downloaded_bytes / update.total_bytes
        if steps and fraction >= steps[0]:
            while steps and fraction >= steps[0]:
                steps.pop(0)
            ctx.log(f"downloading {url}: {fraction:.0%} of {_size(update.total_bytes)}")

    ctx.log(f"downloading {url}")
    try:
        path = ytdlp.download(url, directory, progress)
    except YtDlpError as e:
        raise DownloadError(str(e)) from e
    ctx.log(f"downloaded {url}: {path.name}, {_size(path.stat().st_size)}")
    return path
