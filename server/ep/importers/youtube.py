"""YouTube import: a video or a playlist, downloaded with yt-dlp into the library.

Input: a canonical YouTube video or playlist URL (``ep.metadata.ytdlp.youtube_url``).

- **A video** is one recording without release group (a loose track), or, if it has
  chapters, a single-file album: one file, a ``slice`` per chapter.
- **A playlist** is one release group, one file per entry.

Metadata: yt-dlp's artist/track/album fields where YouTube has them (YouTube Music
"Topic" videos); otherwise "Artist - Title" is parsed from the video title after
removing video noise ("(Official Video)", "[HD]", "lyrics", catalogue numbers, ...),
and the artist falls back to the channel. Then MusicBrainz:

- a loose track: a recording search; exactly one hit with the same title, artist and
  length identifies it; several, or hits without a length, are an
  ``uncertain_metadata`` issue (pick one, keep as entered, skip);
- a chaptered video: a release search with the chapter count, tracks fitting by
  position and length (``ep.importers.resolve``);
- a playlist: a release search without track count, entries paired with release
  tracks by title and length (a playlist may hold more or less than the release).
  Entries that are not on the release found are an ``uncertain_metadata`` issue each:
  import as a recording without release group, or skip.

Not found in MusicBrainz is a normal outcome: imported as entered. Every failure is an
issue on the job, never a silent skip: a video whose metadata cannot be read (removed,
private, blocked) is ``metadata_not_found`` (retry or skip that entry); a failed
download is the framework's ``download_failed``. The rest of a playlist is kept.

Sources: the video link (``youtube``) is the parent of the downloaded ``file``
(``managed``). Covers: the largest thumbnail of the video (or the first entry).
"""

import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import httpx2

from ep.importers.download import ytdlp_download
from ep.importers.framework import (
    Importer,
    ImportFailed,
    JobContext,
    credit_name,
    mb_credit,
    release_label,
    track_label,
)
from ep.importers.proposal import (
    SKIP,
    CandidateType,
    Choice,
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    Origin,
    Proposal,
    Subject,
    SubjectType,
    UrlCover,
    issue_id,
)
from ep.importers.resolve import (
    MIN_SEARCH_SCORE,
    align_by_title,
    mb_label,
    prune_artists,
    search_release,
    use_musicbrainz,
)
from ep.matching.match import AUTO_MATCH_SCORE, DURATION_TOLERANCE_MS, text_score
from ep.matching.normalize import normalize_artist, normalize_title
from ep.metadata import web
from ep.metadata.musicbrainz import (
    MBRecording,
    MBRelease,
    MusicBrainzError,
    lucene_query,
    render_artist_credit,
)
from ep.metadata.ytdlp import (
    PlaylistInfo,
    VideoInfo,
    YouTubeKind,
    YtDlp,
    YtDlpError,
    youtube_url,
)
from ep.models import ImportKind, JobStage, SourceKind

UNKNOWN_ARTIST = "Unknown Artist"
# Candidates shown in an uncertain_metadata issue for a recording.
MAX_RECORDING_CANDIDATES = 5

# Video noise

_NOISE_WORD = (
    r"official|music|lyrics?|audio|video|visuali[sz]er|clip|hd|hq|4k|8k|1080p|720p|m/?v"
    r"|explicit|premiere|out\s+now|free\s+download"
)
# Dashes: hyphen, en dash, em dash.
_DASH = "-\u2013\u2014"
# A bracketed group of noise words only: "(Official Audio)", "[Official Music Video]",
# "(HD)", "【MV】", "(Lyric Video)".
_NOISE_BRACKETED = re.compile(
    rf"\s*[(\[【]\s*(?:(?:{_NOISE_WORD})[\s/&+,.{_DASH}]*)+[)\]】]", re.IGNORECASE
)
# A catalogue number in square brackets: "[d2e006]", "[WARP123]".
_CATALOGUE_NUMBER = re.compile(r"\s*\[(?=[^\]]*[A-Za-z])(?=[^\]]*\d)[A-Za-z0-9.-]{3,16}\]")
# Noise words after a separator at the end ("Title - Official Video", "Title | Lyrics"),
# or a quality mark after a space ("Title HD"; not "Body Music").
_NOISE_TRAILING = re.compile(
    rf"(?:\s+(?P<sep>[{_DASH}|:])\s*(?P<words>(?:(?:{_NOISE_WORD})\b[\s/&+,.]*)+)"
    r"|\s+(?:hd|hq|4k|8k|1080p|720p))$",
    re.IGNORECASE,
)
_QUALITY = re.compile(r"^(?:hd|hq|4k|8k|1080p|720p)$", re.IGNORECASE)


def _strip_trailing_noise(text: str) -> str:
    """``_NOISE_TRAILING`` removed, except one word after the only dash: in "Madonna -
    Music" or "India.Arie - Video" that word is the title, not noise ("Artist - Title -
    Video" and "Title - Official Video" are noise)."""
    m = _NOISE_TRAILING.search(text)
    if m is None:
        return text
    rest = text[: m.start()]
    words = (m.group("words") or "").split()
    single = len(words) == 1 and not _QUALITY.match(words[0])
    if single and m.group("sep") in _DASH and not any(s in rest for s in _SEPARATORS):
        return text
    return rest


# "Premiere: ", "PREMIERE | ", "Exclusive - " at the start.
_NOISE_LEADING = re.compile(rf"^(?:premiere|exclusive|new)\s*[:|{_DASH}]\s*", re.IGNORECASE)
# "(Full Album)" or " - Full Album" and everything after it (genre lists, years).
_FULL_ALBUM = re.compile(
    rf"\s*(?:[{_DASH}|]\s*)?[(\[]?\s*full\s+(?:album|ep|lp)\b.*$", re.IGNORECASE
)
# Chapter numbering: "Track 01: ", "01. ", "1) ", "03 - ".
_CHAPTER_NUMBER = re.compile(r"^(?:track\s*)?\d{1,3}\s*(?:[.):]|\s-)\s*", re.IGNORECASE)
_SEPARATORS = tuple(f" {dash} " for dash in _DASH)
_QUOTED_TITLE = re.compile(r"^(.+?)\s+[\"“](.+)[\"”]$")
_CHANNEL_SUFFIX = re.compile(r"(?:\s+-\s+Topic|VEVO)$")


def clean_title(title: str) -> str:
    """A video title without video noise: "Artist - Title (Official Video) [HD]" ->
    "Artist - Title". Keeps everything else, e.g. "(Live)" or "(Remix)"."""
    text = _NOISE_LEADING.sub("", title.strip())
    text = _FULL_ALBUM.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _NOISE_BRACKETED.sub("", text)
        text = _CATALOGUE_NUMBER.sub("", text)
        text = _strip_trailing_noise(text)
    text = " ".join(text.split()).strip(f" |:{_DASH}")
    return text or title.strip()


def split_artist_title(text: str) -> tuple[str | None, str]:
    """("Artist", "Title") from "Artist - Title" or 'Artist "Title"'; (None, text) if
    there is no artist in it."""
    for separator in _SEPARATORS:
        artist, found, title = text.partition(separator)
        if found and artist.strip() and title.strip():
            return artist.strip(), title.strip()
    if m := _QUOTED_TITLE.match(text):
        return m.group(1).strip(), m.group(2).strip()
    return None, text


def clean_chapter_title(title: str) -> str:
    return clean_title(_CHAPTER_NUMBER.sub("", title.strip())) or title.strip()


def channel_artist(name: str | None) -> str:
    """The artist a channel stands for: "Ernest Hood - Topic" -> "Ernest Hood"."""
    return _CHANNEL_SUFFIX.sub("", name).strip() if name else UNKNOWN_ARTIST


def video_artists_title(info: VideoInfo, artist: str | None = None) -> tuple[list[str], str]:
    """The artists and title of a video: yt-dlp's music fields, else "Artist - Title"
    from the cleaned title, else ``artist`` (a playlist's) or the channel."""
    if info.track and info.artists:
        return list(info.artists), info.track
    parsed_artist, title = split_artist_title(clean_title(info.title))
    if info.artists:
        return list(info.artists), title
    if parsed_artist:
        return [parsed_artist], title
    return [artist or channel_artist(info.channel or info.uploader)], title


def credit_parts(proposal: Proposal, names: Sequence[str]) -> list[IncomingCreditPart]:
    """One incoming artist per name, joined like MusicBrainz credits ("A, B & C")."""
    parts = []
    for index, name in enumerate(names):
        key = f"name:{name.casefold()}"
        if not any(artist.key == key for artist in proposal.artists):
            proposal.artists.append(IncomingArtist(key=key, name=name))
        join = ", " if index < len(names) - 2 else (" & " if index == len(names) - 2 else "")
        parts.append(IncomingCreditPart(artist_key=key, credited_name=name, join_phrase=join))
    return parts


def _same_names(a: Sequence[str], b: Sequence[str]) -> bool:
    return [normalize_artist(n) for n in a] == [normalize_artist(n) for n in b]


def _medium(url: str) -> IncomingMedium:
    return IncomingMedium(
        key=url,
        path=None,
        managed=True,
        download=True,
        origin=Origin(kind=SourceKind.youtube, uri=url),
    )


def _length(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _recording_candidate(hit: MBRecording) -> IssueCandidate:
    facts = [_length(hit.length), hit.first_release_date, hit.disambiguation or None]
    return IssueCandidate(
        type=CandidateType.musicbrainz_recording,
        id=hit.id,
        title=hit.title,
        subtitle=render_artist_credit(hit.artist_credit),
        detail=" · ".join(fact for fact in facts if fact) or None,
        score=float(hit.score or 0),
    )


class YouTubeImporter(Importer):
    kind = ImportKind.youtube

    def __init__(
        self, ytdlp: YtDlp | None = None, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self.ytdlp = ytdlp or YtDlp()
        self.transport = transport  # for covers (tests)

    def build(self, ctx: JobContext) -> Proposal:
        parsed = youtube_url(ctx.job.input)
        if parsed is None:
            raise ImportFailed(f"not a YouTube video or playlist URL: {ctx.job.input}")
        proposal = Proposal()
        ctx.stage(JobStage.fetch)
        if parsed.kind is YouTubeKind.playlist:
            self._playlist(ctx, proposal, parsed.url)
        elif (info := self._video(ctx, parsed.url, parsed.url)) is not None:
            if info.chapters and len(info.chapters) > 1:
                self._chaptered(ctx, proposal, info, parsed.url)
            else:
                self._single(ctx, proposal, info, parsed.url)
        prune_artists(proposal)
        return proposal

    # Fetch

    def _video(self, ctx: JobContext, url: str, label: str) -> VideoInfo | None:
        """A video's metadata, or None with a ``metadata_not_found`` issue (open, or
        answered with skip)."""
        issue = Issue(
            id=issue_id(IssueKind.metadata_not_found, url),
            kind=IssueKind.metadata_not_found,
            message="",
            subject=Subject(type=SubjectType.medium, key=url, label=label),
            choices=[Choice.retry, Choice.skip],
        )
        if (answer := ctx.answers.get(issue.id)) and answer.choice is Choice.skip:
            issue.message = f"{label}: skipped"
            ctx.raise_issue(issue)
            return None
        try:
            return self.ytdlp.video(url)
        except YtDlpError as e:
            issue.message = f"{label}: the video cannot be read: {e}"
            ctx.raise_issue(issue)
            return None

    def _playlist_info(self, ctx: JobContext, url: str) -> PlaylistInfo | None:
        try:
            return self.ytdlp.playlist(url)
        except YtDlpError as e:
            ctx.raise_issue(
                Issue(
                    id=issue_id(IssueKind.metadata_not_found, url),
                    kind=IssueKind.metadata_not_found,
                    message=f"The playlist cannot be read: {e}",
                    subject=Subject(type=SubjectType.release, key=url, label=url),
                    choices=[Choice.retry],
                )
            )
            return None

    # A single video

    def _single(self, ctx: JobContext, proposal: Proposal, info: VideoInfo, url: str) -> None:
        artists, title = video_artists_title(info)
        track = IncomingTrack(
            key=url,
            medium_key=url,
            position=1,
            title=title,
            credit=credit_parts(proposal, artists),
            length_ms=info.duration_ms,
        )
        ctx.log(f"YouTube: {title!r} by {credit_name(track.credit)} (from {info.title!r})")
        proposal.media.append(_medium(url))
        proposal.loose_tracks.append(track)
        ctx.stage(JobStage.resolve, 1)
        self._identify_recording(ctx, proposal, track)
        ctx.advance()

    def _identify_recording(
        self, ctx: JobContext, proposal: Proposal, track: IncomingTrack
    ) -> None:
        """Take MusicBrainz's recording if one hit fits (see the module docstring)."""
        artist = credit_name(track.credit)
        query = lucene_query(
            recording=track.title, artist=artist if artist != UNKNOWN_ARTIST else None
        )
        try:
            hits = ctx.musicbrainz.search_recordings(query, limit=25)
        except MusicBrainzError as e:
            raise ImportFailed(f"MusicBrainz search failed: {e}") from e
        title, credit = normalize_title(track.title), normalize_artist(artist)
        alike = [
            hit
            for hit in hits
            if (hit.score or 0) >= MIN_SEARCH_SCORE
            and text_score(title, normalize_title(hit.title)) >= AUTO_MATCH_SCORE
            and text_score(credit, normalize_artist(render_artist_credit(hit.artist_credit)))
            >= AUTO_MATCH_SCORE
        ]
        fitting = [
            hit
            for hit in alike
            if hit.length is not None
            and track.length_ms is not None
            and abs(hit.length - track.length_ms) <= DURATION_TOLERANCE_MS
        ]
        unsure = [hit for hit in alike if hit.length is None or track.length_ms is None]
        issue = Issue(
            id=issue_id(IssueKind.uncertain_metadata, track.key),
            kind=IssueKind.uncertain_metadata,
            message=(
                f"MusicBrainz has several recordings like {track_label(track)!r}. Use one, "
                "or import the video as entered."
            ),
            subject=Subject(type=SubjectType.track, key=track.key, label=track_label(track)),
            candidates=[
                _recording_candidate(hit) for hit in [*fitting, *unsure][:MAX_RECORDING_CANDIDATES]
            ],
            choices=[Choice.pick_candidate, Choice.keep_as_entered, Choice.skip],
        )
        chosen: MBRecording | None = None
        if len(fitting) == 1:
            chosen = fitting[0]
        elif fitting or unsure:
            answer = ctx.raise_issue(issue)  # open: matching waits for the answer
            if answer is None or answer.choice is Choice.keep_as_entered:
                return
            if answer.choice is Choice.skip:
                track.target = SKIP
                return
            chosen = next(hit for hit in [*fitting, *unsure] if hit.id == answer.candidate_id)
        if chosen is None:
            ctx.log(f"'{track.title} — {artist}' not found in MusicBrainz; importing it as entered")
            return
        ctx.log(f"found in MusicBrainz: recording {chosen.title!r} ({chosen.id})")
        track.title = chosen.title
        track.credit = mb_credit(proposal, chosen.artist_credit)
        track.mbid = chosen.id
        track.isrcs = sorted({*track.isrcs, *chosen.isrcs})

    # A video with chapters: a single-file album

    def _chaptered(self, ctx: JobContext, proposal: Proposal, info: VideoInfo, url: str) -> None:
        assert info.chapters
        artists, title = video_artists_title(info)
        if info.album:
            title = info.album
        credit = credit_parts(proposal, info.album_artists or artists)
        tracks = []
        for index, chapter in enumerate(info.chapters, start=1):
            start = round(chapter.start_time * 1000)
            end = round(chapter.end_time * 1000) if chapter.end_time is not None else None
            stop = end if end is not None else info.duration_ms
            track_artist, track_title = split_artist_title(clean_chapter_title(chapter.title))
            track_credit = credit
            if track_artist is not None and not _same_names([track_artist], artists):
                track_credit = credit_parts(proposal, [track_artist])
            tracks.append(
                IncomingTrack(
                    key=f"{url}#{index}",
                    medium_key=url,
                    start_ms=start,
                    end_ms=end,
                    position=index,
                    title=track_title or f"Track {index}",
                    credit=track_credit,
                    length_ms=stop - start if stop is not None else None,
                )
            )
        thumbnail = info.best_thumbnail
        release = IncomingRelease(
            key=url,
            title=title,
            credit=credit,
            cover=UrlCover(url=thumbnail) if thumbnail else None,
            tags=list(info.tags),
            tracks=tracks,
        )
        ctx.log(
            f"YouTube: {title!r} by {credit_name(credit)}, {len(tracks)} chapters "
            f"(from {info.title!r})"
        )
        proposal.media.append(_medium(url))
        proposal.releases.append(release)
        ctx.stage(JobStage.resolve, 1)
        subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
        mb_release = search_release(
            ctx, release, subject, artist=_search_artist(release), entered="chapters"
        )
        if mb_release is not None:
            use_musicbrainz(ctx, proposal, release, mb_release)
        ctx.advance()

    # A playlist: one release group

    def _playlist(self, ctx: JobContext, proposal: Proposal, url: str) -> None:
        playlist = self._playlist_info(ctx, url)
        if playlist is None:
            return
        ctx.stage(JobStage.fetch, len(playlist.entries))
        entries: list[tuple[int, str, VideoInfo]] = []
        for index, entry in enumerate(playlist.entries, start=1):
            parsed = youtube_url(entry.url)
            entry_url = parsed.url if parsed else f"https://www.youtube.com/watch?v={entry.id}"
            info = self._video(ctx, entry_url, f"{index}. {entry.title or entry_url}")
            if info is not None:
                entries.append((index, entry_url, info))
            ctx.advance()
        infos = [info for _, _, info in entries]
        parsed_artist, parsed_title = split_artist_title(clean_title(playlist.title))
        title = _common([i.album for i in infos], len(infos)) or parsed_title
        artists = _common_artists(infos) or (
            [parsed_artist] if parsed_artist else [channel_artist(playlist.channel)]
        )
        credit = credit_parts(proposal, artists)
        tracks = []
        for index, entry_url, info in entries:
            track_artists, track_title = video_artists_title(info, credit_name(credit))
            same = _same_names(track_artists, artists)
            tracks.append(
                IncomingTrack(
                    key=entry_url,
                    medium_key=entry_url,
                    position=index,
                    title=track_title,
                    credit=credit if same else credit_parts(proposal, track_artists),
                    length_ms=info.duration_ms,
                )
            )
            proposal.media.append(_medium(entry_url))
        thumbnail = next((i.best_thumbnail for i in infos if i.best_thumbnail), None)
        release = IncomingRelease(
            key=url,
            title=title,
            credit=credit,
            cover=UrlCover(url=thumbnail) if thumbnail else None,
            tracks=tracks,
        )
        proposal.releases.append(release)
        ctx.log(
            f"YouTube playlist: {title!r} by {credit_name(credit)}, {len(tracks)} of "
            f"{len(playlist.entries)} entries read (from {playlist.title!r})"
        )
        if not tracks:
            return
        ctx.stage(JobStage.resolve, 1)
        subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
        mb_release = search_release(
            ctx,
            release,
            subject,
            artist=_search_artist(release),
            track_count=False,
            fits=_most_tracks_by_title,
            entered="playlist",
        )
        if mb_release is not None:
            aligned = align_by_title(release, mb_release)
            for track in [t for t in release.tracks if t.key not in aligned]:
                self._not_on_release(ctx, proposal, release, track, mb_release)
            use_musicbrainz(ctx, proposal, release, mb_release, aligned)
        ctx.advance()

    def _not_on_release(
        self,
        ctx: JobContext,
        proposal: Proposal,
        release: IncomingRelease,
        track: IncomingTrack,
        mb_release: MBRelease,
    ) -> None:
        """A playlist entry that is not a track of the MusicBrainz release: an issue; it
        becomes a recording without release group, or is skipped."""
        issue = Issue(
            id=issue_id(IssueKind.uncertain_metadata, track.key),
            kind=IssueKind.uncertain_metadata,
            message=(
                f"{track_label(track)!r} is not a track of {mb_label(mb_release)!r} on "
                "MusicBrainz. Import it as a recording without release group, or skip it."
            ),
            subject=Subject(type=SubjectType.track, key=track.key, label=track_label(track)),
            choices=[Choice.keep_as_entered, Choice.skip],
        )
        answer = ctx.raise_issue(issue)  # while open, matching leaves the track alone
        release.tracks.remove(track)
        proposal.loose_tracks.append(track)
        if answer is not None and answer.choice is Choice.skip:
            track.target = SKIP

    def fetch_cover(self, url: str) -> bytes:
        return web.fetch(url, self.transport)

    def download(self, ctx: JobContext, medium: IncomingMedium, directory: Path) -> Path:
        assert medium.origin is not None
        return ytdlp_download(ctx, self.ytdlp, medium.origin.uri, directory)


def _search_artist(release: IncomingRelease) -> str | None:
    artist = credit_name(release.credit)
    return artist if artist != UNKNOWN_ARTIST else None


def _most_tracks_by_title(release: IncomingRelease, mb_release: MBRelease) -> bool:
    """A playlist fits a release if at least half of its entries are tracks of it."""
    aligned = align_by_title(release, mb_release)
    return bool(aligned) and 2 * len(aligned) >= len(release.tracks)


def _common(values: Sequence[str | None], total: int) -> str | None:
    """The value at least half of ``total`` items share, or None."""
    counts = Counter(value for value in values if value)
    if counts and (best := counts.most_common(1)[0])[1] * 2 >= total:
        return best[0]
    return None


def _common_artists(infos: Sequence[VideoInfo]) -> list[str] | None:
    """The album artists (or else artists) at least half of the videos share."""
    credits = [tuple(i.album_artists or i.artists) or None for i in infos]
    counts = Counter(credit for credit in credits if credit)
    if counts and (best := counts.most_common(1)[0])[1] * 2 >= len(infos):
        return list(best[0])
    return None
