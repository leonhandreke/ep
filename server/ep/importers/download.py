"""Downloads with yt-dlp for importers (Bandcamp, YouTube, NTS): progress into the job log."""

from pathlib import Path

from ep.importers.framework import DownloadError, JobContext
from ep.metadata import bandcamp, nts
from ep.metadata.ytdlp import (
    DOWNLOAD_TIMEOUT,
    Progress,
    YouTubeKind,
    YtDlp,
    YtDlpError,
    youtube_url,
)

# Progress is logged at these fractions of a download.
PROGRESS_STEPS = (0.25, 0.5, 0.75)
# Item progress (``import_job.progress_item``, not logged) is saved this often.
PROGRESS_INTERVAL = 0.02


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def is_downloadable(url: str) -> bool:
    """Whether ``url`` is a canonical URL of a host EP downloads from: a YouTube video, a
    Bandcamp track, a SoundCloud track, an NTS episode page."""
    parsed = youtube_url(url)
    video = parsed.url if parsed and parsed.kind is YouTubeKind.video else None
    ref = nts.episode_ref(url)
    return url in (
        video,
        bandcamp.canonical_url(url),
        nts.soundcloud_url(url),
        ref.url if ref else None,
    )


def ytdlp_download(
    ctx: JobContext,
    ytdlp: YtDlp,
    url: str,
    directory: Path,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> Path:
    """The audio of ``url`` (a canonical URL, ``is_downloadable``: only hosts EP knows
    reach yt-dlp) downloaded into ``directory`` (see ``YtDlp.download``, which kills
    yt-dlp after ``timeout`` seconds); ``DownloadError`` with yt-dlp's message on
    failure."""
    if not is_downloadable(url):
        raise DownloadError(f"not a canonical YouTube, Bandcamp, SoundCloud or NTS URL: {url!r}")
    steps = list(PROGRESS_STEPS)
    reported = [0.0]

    def progress(update: Progress) -> None:
        if not update.total_bytes:
            return
        fraction = update.downloaded_bytes / update.total_bytes
        if steps and fraction >= steps[0]:
            while steps and fraction >= steps[0]:
                steps.pop(0)
            ctx.job.progress_item = fraction
            ctx.log(f"downloading {url}: {fraction:.0%} of {_size(update.total_bytes)}")
        elif fraction - reported[0] >= PROGRESS_INTERVAL:
            ctx.item_progress(fraction)
        else:
            return
        reported[0] = fraction

    ctx.log(f"downloading {url}")
    try:
        path = ytdlp.download(url, directory, progress, timeout)
    except YtDlpError as e:
        raise DownloadError(str(e)) from e
    ctx.log(f"downloaded {url}: {path.name}, {_size(path.stat().st_size)}")
    return path
