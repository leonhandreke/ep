"""Bandcamp import: an album (or track) page, downloaded with yt-dlp into the library.

Input: a Bandcamp album or track page URL (canonical, ``ep.metadata.bandcamp``). The
importer

1. fetches the page and reads the release from it (``ep.metadata.bandcamp``): the
   artist is the release's artist field, never the page owner (often a label, kept as
   ``label``), tags are kept in the proposal (``tags``) for later. A page that is gone
   is a ``metadata_not_found`` issue, one that cannot be fetched (a bot check, the
   network) a ``fetch_failed`` issue; both offer a retry;
2. resolves it to MusicBrainz: the releases linked to the page URL, then the barcode,
   then a search (``ep.importers.resolve``); the first whose tracks fit by position
   and length is used. Not in MusicBrainz is a normal outcome: the release is imported
   as entered on Bandcamp, without MBIDs;
3. describes one download per track: the track page is the origin, a ``bandcamp``
   source that becomes the parent of the downloaded ``file`` source (``managed``).
   Tracks that cannot be streamed (pre-order tracks not released yet) are imported
   without audio (logged);
4. downloads each track with ``yt-dlp -x`` (Bandcamp streams MP3: kept as is).

A track page that is part of an album becomes a recording without release group (the
album page imports the release group); a standalone track page is a single.

Matching (an album track already in the library as a loose recording, e.g. from
YouTube, gains the track instead of a duplicate) and applying are the framework's. The
cover is the page's artwork (``UrlCover``).
"""

from pathlib import Path

import httpx2

from ep.importers.download import ytdlp_download
from ep.importers.framework import (
    Importer,
    ImportFailed,
    JobContext,
    credit_name,
    release_label,
)
from ep.importers.proposal import (
    Choice,
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueKind,
    LogLevel,
    Origin,
    Proposal,
    Subject,
    SubjectType,
    UrlCover,
    issue_id,
)
from ep.importers.resolve import first_fitting, prune_artists, search_release, use_musicbrainz
from ep.metadata import web
from ep.metadata.bandcamp import (
    Bandcamp,
    BandcampError,
    BandcampNotFound,
    BandcampRelease,
    BandcampUnreachable,
    similar_names,
)
from ep.metadata.musicbrainz import MusicBrainzError
from ep.metadata.ytdlp import YtDlp
from ep.models import ImportKind, JobStage, ReleaseGroupType, SourceKind

_VARIOUS = {"various artists", "various", "v/a", "va"}


def artist_credit(proposal: Proposal, name: str) -> list[IncomingCreditPart]:
    """A credit of one incoming artist for a name as entered ("A, B & C" is not split:
    MusicBrainz, if it knows the release, gives the real credit)."""
    key = f"name:{name.casefold()}"
    if not any(artist.key == key for artist in proposal.artists):
        proposal.artists.append(IncomingArtist(key=key, name=name))
    return [IncomingCreditPart(artist_key=key, credited_name=name)]


class BandcampImporter(Importer):
    kind = ImportKind.bandcamp

    def __init__(
        self, ytdlp: YtDlp | None = None, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self.ytdlp = ytdlp or YtDlp()
        self.transport = transport  # for the page and the cover (tests)

    def build(self, ctx: JobContext) -> Proposal:
        url = ctx.job.input
        proposal = Proposal()
        ctx.stage(JobStage.fetch)
        try:
            with Bandcamp(transport=self.transport) as bandcamp:
                page = bandcamp.get_release(url)
        except BandcampNotFound as e:
            ctx.raise_issue(
                Issue(
                    id=issue_id(IssueKind.metadata_not_found, url),
                    kind=IssueKind.metadata_not_found,
                    message=f"The Bandcamp page is not there (any more): {e}",
                    subject=Subject(type=SubjectType.release, key=url, label=url),
                    choices=[Choice.retry],
                )
            )
            return proposal
        except BandcampUnreachable as e:
            # A bot check, the network, a server error: the inbox offers a retry.
            ctx.raise_issue(
                Issue(
                    id=issue_id(IssueKind.fetch_failed, url),
                    kind=IssueKind.fetch_failed,
                    message=f"The Bandcamp page could not be fetched: {e}",
                    subject=Subject(type=SubjectType.release, key=url, label=url),
                    choices=[Choice.retry],
                )
            )
            return proposal
        except BandcampError as e:
            raise ImportFailed(f"Bandcamp: {e}") from e
        ctx.log(
            f"Bandcamp: {page.title!r} by {page.artist}, {len(page.tracks)} track(s)"
            + (f", label {page.label}" if page.label else "")
        )
        if page.hidden_tracks:
            ctx.log(
                f"{page.hidden_tracks} track(s) are hidden on the page (only in the paid "
                "download) and are not imported",
                LogLevel.warning,
            )
        if page.preorder:
            ctx.log("a pre-order: tracks not released yet cannot be downloaded", LogLevel.warning)
        tracks = self._tracks(ctx, proposal, page)
        if page.kind == "track" and page.album_url:
            ctx.log(
                f"this track is part of the album {page.album_url}: imported as a recording "
                "without release group (import the album page for the release group)",
                LogLevel.warning,
            )
            proposal.loose_tracks = tracks
        else:
            release = IncomingRelease(
                key=page.url,
                title=page.title,
                credit=artist_credit(proposal, page.artist),
                type=_type(page),
                date=page.date,
                barcode=page.barcode,
                cover=UrlCover(url=page.cover_url) if page.cover_url else None,
                label=page.label,
                tags=page.tags,
                tracks=tracks,
            )
            proposal.releases.append(release)
            ctx.stage(JobStage.resolve, 1)
            self._resolve(ctx, proposal, release)
            ctx.advance()
        prune_artists(proposal)
        return proposal

    def _tracks(
        self, ctx: JobContext, proposal: Proposal, page: BandcampRelease
    ) -> list[IncomingTrack]:
        release_credit = artist_credit(proposal, page.artist)
        tracks = []
        for track in page.tracks:
            medium_key = track.url if track.streamable else None
            if medium_key is None:
                ctx.log(
                    f"track {track.number} ({track.title!r}) cannot be streamed (not "
                    "released yet): imported without audio; import the page again once "
                    "it is out",
                    LogLevel.warning,
                )
            else:
                proposal.media.append(
                    IncomingMedium(
                        key=medium_key,
                        path=None,
                        managed=True,
                        download=True,
                        origin=Origin(kind=SourceKind.bandcamp, uri=medium_key),
                    )
                )
            credit = release_credit
            if track.artist and not similar_names(track.artist, page.artist):
                credit = artist_credit(proposal, track.artist)
            tracks.append(
                IncomingTrack(
                    key=track.url or f"{page.url}#{track.number}",
                    medium_key=medium_key,
                    position=track.number,
                    title=track.title,
                    credit=credit,
                    length_ms=track.length_ms,
                    isrcs=[track.isrc] if track.isrc else [],
                )
            )
        return tracks

    def _resolve(self, ctx: JobContext, proposal: Proposal, release: IncomingRelease) -> None:
        """MusicBrainz by URL, then barcode, then search; else as entered."""
        try:
            linked = ctx.musicbrainz.lookup_url(release.key)
            mb_release = first_fitting(ctx, release, [r.id for r in linked], "its Bandcamp URL")
            by_barcode = (
                ctx.musicbrainz.releases_by_barcode(release.barcode)
                if mb_release is None and release.barcode
                else []
            )
        except MusicBrainzError as e:
            raise ImportFailed(f"MusicBrainz lookup failed: {e}") from e
        if by_barcode:
            count = len(release.tracks)
            fitting = [r.id for r in by_barcode if sum(m.track_count for m in r.media) == count]
            mb_release = first_fitting(ctx, release, fitting, f"barcode {release.barcode}")
        if mb_release is None:
            subject = Subject(
                type=SubjectType.release, key=release.key, label=release_label(release)
            )
            mb_release = search_release(
                ctx,
                release,
                subject,
                artist=credit_name(release.credit),
                entered="Bandcamp metadata",
            )
        if mb_release is not None:
            use_musicbrainz(ctx, proposal, release, mb_release)

    def fetch_cover(self, url: str) -> bytes:
        return web.fetch(url, self.transport)

    def download(self, ctx: JobContext, medium: IncomingMedium, directory: Path) -> Path:
        assert medium.origin is not None
        return ytdlp_download(ctx, self.ytdlp, medium.origin.uri, directory)


def _type(page: BandcampRelease) -> ReleaseGroupType:
    if page.kind == "track":
        return ReleaseGroupType.single
    if page.artist.casefold() in _VARIOUS:
        return ReleaseGroupType.compilation
    return ReleaseGroupType.album
