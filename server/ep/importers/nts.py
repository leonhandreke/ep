"""NTS import: a radio episode, its audio and its tracklist.

Input: an NTS episode page URL (canonical, ``ep.metadata.nts.episode_ref``). The
importer

1. fetches the episode (with its tracklist) and its show from NTS's API
   (``ep.metadata.nts``). An episode that is not there is a ``metadata_not_found``
   issue, one that cannot be fetched a ``fetch_failed`` issue, a response in a shape
   EP does not know (the API is undocumented) an ``unexpected_response`` issue; all
   offer a retry. Show and episode images are fetched into the job's work directory;
2. describes one download: the episode audio, from its SoundCloud upload (or, without
   one, from the episode page, which yt-dlp follows to wherever NTS hosts it), with
   ``yt-dlp -x`` into ``_radio/NTS/<Show>/<YYYY-MM-DD Episode>.<ext>``. Sources: an
   ``nts`` link (the episode page) → the ``soundcloud`` link → the ``file``;
3. describes each tracklist entry as an ``optional`` loose track: the station's artist
   and title, the ISRC, Deezer, ACRCloud and NTS ids (matched, and stored as external
   IDs), the MusicBrainz recording MBID, and the time range, a ``slice`` of the episode
   file: the detected ``offset``/``duration``, else NTS's estimate (flagged
   ``offset_is_estimate``); an entry without either has no slice. The detected
   duration is a lower bound for the recording's length (plays are often cut short),
   never its length;
4. the framework matches the entries (MBID, then ISRC/Deezer/ACRCloud/NTS ids, then
   fuzzy artist + title): confident matches are used, clear misses become new
   standalone recordings (and artists), and only the uncertain middle raises an issue.
   Those issues do not block (``optional``): the episode applies with the entry
   unmatched (its row and slice exist, without a recording), and the job stays in
   ``needs_review`` until the issue is answered; running it again with the answer adds
   the recording and reuses everything else;
5. ``apply`` writes the radio rows (``ep.models.radio``): the show and the episode
   (found by station + slug and by URL: importing an episode again updates it), and one
   ``radio_episode_track`` per entry with the station's text, the recording and the
   slice. Entries already in the library from an earlier import (same text) keep their
   recording and time range (as edited since, e.g. with
   ``PATCH /radio/episode-tracks/{id}``). Slices and radio-only recordings that no entry
   plays any more (the station corrected or removed an entry) are deleted
   (``ep.radio.delete_unused_recordings``).

NTS genres and moods (``radio.episode.genres``/``moods``) become tags of the episode
when the import creates it; importing it again leaves its tags alone (``ep.notes.tags``).
"""

from pathlib import Path, PurePath

import httpx2
from sqlalchemy.orm import selectinload
from sqlmodel import select

from ep import covers
from ep.importers.download import ytdlp_download
from ep.importers.framework import (
    Applied,
    Importer,
    ImportFailed,
    JobContext,
    existing,
    fetch_incoming_image,
    safe_name,
)
from ep.importers.proposal import (
    Choice,
    IncomingArtist,
    IncomingCreditPart,
    IncomingExternalId,
    IncomingImage,
    IncomingMedium,
    IncomingRadio,
    IncomingRadioEntry,
    IncomingRadioEpisode,
    IncomingRadioShow,
    IncomingTrack,
    Issue,
    IssueKind,
    LogLevel,
    Origin,
    Proposal,
    Subject,
    SubjectType,
    issue_id,
)
from ep.metadata import web
from ep.metadata.nts import (
    EpisodeRef,
    Nts,
    NtsEpisode,
    NtsNotFound,
    NtsShow,
    NtsTrack,
    NtsUnexpectedResponse,
    NtsUnreachable,
    episode_ref,
)
from ep.metadata.ytdlp import YtDlp
from ep.models import (
    EntityType,
    ImportKind,
    JobStage,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    RadioStation,
    Source,
    SourceKind,
    rel,
)
from ep.notes.tags import add_imported_tags
from ep.radio import delete_unused_recordings, delete_unused_slices, radio_only_ids

# Seconds an episode download may take (two hours of audio, ~150 MB).
EPISODE_DOWNLOAD_TIMEOUT = 6 * 3600
# MusicBrainz's names for missing text.
UNKNOWN_ARTIST = "[unknown]"
UNTITLED = "[untitled]"


def _credit(proposal: Proposal, name: str) -> list[IncomingCreditPart]:
    """One incoming artist for the artist text as given ("A, B" is not split: the
    matcher compares it with whole credits and their single artists)."""
    key = f"name:{name.casefold()}"
    if not any(artist.key == key for artist in proposal.artists):
        proposal.artists.append(IncomingArtist(key=key, name=name))
    return [IncomingCreditPart(artist_key=key, credited_name=name)]


def _external_ids(entry: NtsTrack) -> list[IncomingExternalId]:
    ids = []
    if entry.deezer_track_id is not None:
        ids.append(IncomingExternalId(scheme="deezer", value=str(entry.deezer_track_id)))
    if entry.acr_id:
        ids.append(IncomingExternalId(scheme="acr", value=entry.acr_id))
    if entry.uid:
        ids.append(IncomingExternalId(scheme="nts", value=entry.uid))
    return ids


def time_ranges(tracklist: list[NtsTrack]) -> list[tuple[int | None, int | None]]:
    """(start, end) in ms of each entry within the episode: offset plus duration (detected,
    else estimated); without a duration, up to the next entry's start (or open-ended for
    the last one); (None, None) without an offset."""
    starts = [entry.start_ms for entry in tracklist]
    ranges: list[tuple[int | None, int | None]] = []
    for index, entry in enumerate(tracklist):
        start, duration = starts[index], entry.duration_ms
        if start is None:
            ranges.append((None, None))
            continue
        end = start + duration if duration else None
        if end is None:
            # The next entry to start (the tracklist may be out of order).
            later = [s for s in starts if s is not None and s > start]
            end = min(later) if later else None
        ranges.append((start, end))
    return ranges


class NtsImporter(Importer):
    kind = ImportKind.nts

    def __init__(
        self, ytdlp: YtDlp | None = None, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self.ytdlp = ytdlp or YtDlp()
        self.transport = transport  # NTS's API and images (tests)

    def build(self, ctx: JobContext) -> Proposal:
        ref = episode_ref(ctx.job.input)
        if ref is None:
            raise ImportFailed(f"not an NTS episode URL: {ctx.job.input}")
        proposal = Proposal()
        ctx.stage(JobStage.fetch)
        fetched = self._fetch(ctx, ref)
        if fetched is None:
            return proposal
        episode, show = fetched
        ctx.log(
            f"NTS: {show.name!r}, episode {episode.name!r} of "
            f"{episode.broadcast:%Y-%m-%d}, {len(episode.tracklist)} tracklist entries"
            if episode.broadcast
            else f"NTS: {show.name!r}, episode {episode.name!r}, "
            f"{len(episode.tracklist)} tracklist entries"
        )
        medium = self._medium(ctx, ref, episode)
        proposal.media.append(medium)
        proposal.radio = IncomingRadio(
            show=IncomingRadioShow(
                station=RadioStation.nts,
                slug=ref.show,
                name=show.name,
                description=show.description or "",
                image=IncomingImage(url=show.media.image_url) if show.media.image_url else None,
            ),
            episode=IncomingRadioEpisode(
                slug=ref.episode,
                name=episode.name,
                description=episode.description or "",
                broadcast_at=episode.broadcast,
                location=episode.location_long,
                url=ref.url,
                image=(
                    IncomingImage(url=episode.media.image_url) if episode.media.image_url else None
                ),
                genres=[genre.value for genre in episode.genres],
                moods=[mood.value for mood in episode.moods],
            ),
            medium_key=medium.key,
        )
        self._tracklist(ctx, proposal, ref, episode, medium.key)
        for image in (proposal.radio.show.image, proposal.radio.episode.image):
            if image is not None:
                fetch_incoming_image(ctx, self, image)
        return proposal

    def _fetch(self, ctx: JobContext, ref: EpisodeRef) -> tuple[NtsEpisode, NtsShow] | None:
        subject = Subject(type=SubjectType.radio_episode, key=ref.url, label=ref.url)

        def issue(kind: IssueKind, message: str) -> None:
            ctx.raise_issue(
                Issue(
                    id=issue_id(kind, ref.url),
                    kind=kind,
                    message=message,
                    subject=subject,
                    choices=[Choice.retry],
                )
            )

        try:
            with Nts(transport=self.transport) as nts:
                return nts.get_episode(ref), nts.get_show(ref.show)
        except NtsNotFound as e:
            issue(IssueKind.metadata_not_found, f"The NTS episode is not there (any more): {e}")
        except NtsUnreachable as e:
            # The network, a server error, a bot check: the inbox offers a retry.
            issue(IssueKind.fetch_failed, f"The NTS episode could not be fetched: {e}")
        except NtsUnexpectedResponse as e:
            issue(
                IssueKind.unexpected_response,
                f"NTS's API answered in a shape EP does not know (it is undocumented and "
                f"may have changed): {e}",
            )
        return None

    def _medium(self, ctx: JobContext, ref: EpisodeRef, episode: NtsEpisode) -> IncomingMedium:
        if (url := episode.soundcloud_url) is not None:
            origin = Origin(kind=SourceKind.soundcloud, uri=url)
        else:
            sources = ", ".join(a.source for a in episode.audio_sources) or "none"
            ctx.log(
                f"no SoundCloud upload (audio sources: {sources}): downloading from the "
                "episode page",
                LogLevel.warning,
            )
            origin = Origin(kind=SourceKind.nts, uri=ref.url)
        return IncomingMedium(key=ref.url, path=None, managed=True, download=True, origin=origin)

    def _tracklist(
        self,
        ctx: JobContext,
        proposal: Proposal,
        ref: EpisodeRef,
        episode: NtsEpisode,
        medium_key: str,
    ) -> None:
        assert proposal.radio is not None
        known = self._known_entries(ctx, ref)
        for position, (entry, (start, end)) in enumerate(
            zip(episode.tracklist, time_ranges(episode.tracklist), strict=True), start=1
        ):
            artist = entry.artist.strip() or UNKNOWN_ARTIST
            title = entry.title.strip() or UNTITLED
            track = IncomingTrack(
                key=f"{ref.url}#{entry.uid or position}",
                medium_key=medium_key if start is not None else None,
                start_ms=start,
                end_ms=end,
                position=position,
                title=title,
                credit=_credit(proposal, artist),
                isrcs=[entry.isrc_id] if entry.isrc_id else [],
                external_ids=_external_ids(entry),
                mbid=entry.musicbrainz_track_id,
                min_length_ms=entry.detected_duration_ms,
                optional=True,
            )
            # The same entry as imported before: same text, at this position if it is
            # still there (the station may have added or removed entries before it).
            same = known.get((artist, title), [])
            row = next((r for r in same if r.position == position), same[0] if same else None)
            if row is not None:
                same.remove(row)
                # In the library from an earlier import: keep its recording and range.
                if row.recording is not None:
                    track.target = existing(row.recording)
                if row.source is not None and row.source.kind is SourceKind.slice:
                    track.start_ms, track.end_ms = row.source.start_ms, row.source.end_ms
                    start, end = track.start_ms, track.end_ms
            proposal.loose_tracks.append(track)
            proposal.radio.entries.append(
                IncomingRadioEntry(
                    position=position,
                    track_key=track.key,
                    artist_text=artist,
                    title_text=title,
                    start_ms=start,
                    end_ms=end,
                    offset_is_estimate=entry.estimate,
                )
            )
        estimated = sum(entry.estimate for entry in episode.tracklist)
        untimed = sum(entry.start_ms is None for entry in episode.tracklist)
        if estimated or untimed:
            ctx.log(f"{estimated} entries have estimated times, {untimed} have none (no slice)")

    def _known_entries(
        self, ctx: JobContext, ref: EpisodeRef
    ) -> dict[tuple[str, str], list[RadioEpisodeTrack]]:
        """The tracklist of the episode as imported before, by artist and title text."""
        episode = ctx.session.exec(
            select(RadioEpisode)
            .where(RadioEpisode.url == ref.url)
            .options(
                selectinload(rel(RadioEpisode.tracks)).selectinload(rel(RadioEpisodeTrack.source)),
                selectinload(rel(RadioEpisode.tracks)).selectinload(
                    rel(RadioEpisodeTrack.recording)
                ),
            )
        ).first()
        known: dict[tuple[str, str], list[RadioEpisodeTrack]] = {}
        for track in episode.tracks if episode else []:
            known.setdefault((track.artist_text, track.title_text), []).append(track)
        return known

    def fetch_cover(self, url: str) -> bytes:
        return web.fetch(url, self.transport)

    def download(self, ctx: JobContext, medium: IncomingMedium, directory: Path) -> Path:
        assert medium.origin is not None
        return ytdlp_download(
            ctx, self.ytdlp, medium.origin.uri, directory, timeout=EPISODE_DOWNLOAD_TIMEOUT
        )

    def library_path(self, proposal: Proposal, medium: IncomingMedium) -> PurePath:
        assert proposal.radio is not None
        episode = proposal.radio.episode
        name = episode.name
        if episode.broadcast_at is not None:
            name = f"{episode.broadcast_at:%Y-%m-%d} {name}"
        return PurePath("_radio", "NTS", safe_name(proposal.radio.show.name), safe_name(name))

    def apply(self, ctx: JobContext, proposal: Proposal, applied: Applied) -> None:
        radio = proposal.radio
        if radio is None:
            return
        session = ctx.session
        show = session.exec(
            select(RadioShow).where(
                RadioShow.station == radio.show.station, RadioShow.slug == radio.show.slug
            )
        ).first()
        if show is None:
            show = RadioShow(station=radio.show.station, slug=radio.show.slug, name=radio.show.name)
            session.add(show)
        show.name, show.description = radio.show.name, radio.show.description
        self._image(ctx, "radio-show", show, radio.show.image)

        incoming = radio.episode
        episode = session.exec(select(RadioEpisode).where(RadioEpisode.url == incoming.url)).first()
        if episode is None:
            episode = RadioEpisode(
                show_id=show.id, slug=incoming.slug, name=incoming.name, url=incoming.url
            )
            session.add(episode)
            # Only for an episode the import creates: re-importing keeps the user's tags.
            add_imported_tags(
                session, EntityType.radio_episode, episode.id, [*incoming.genres, *incoming.moods]
            )
        episode.show_id = show.id
        episode.slug, episode.name = incoming.slug, incoming.name
        episode.description, episode.location = incoming.description, incoming.location
        episode.broadcast_at = incoming.broadcast_at
        self._image(ctx, "radio-episode", episode, incoming.image)
        if (file := applied.files.get(radio.medium_key)) is not None:
            episode.source_id = file.id
            self._link_page(ctx, file, incoming.url)
        session.flush()

        rows = {track.position: track for track in episode.tracks}
        # What the rows played before: slices and radio-only recordings no entry uses
        # any more are deleted below (``ep.radio``).
        old_recordings = radio_only_ids(
            session, (row.recording_id for row in rows.values() if row.recording_id)
        )
        old_slices = {row.source_id for row in rows.values() if row.source_id is not None}
        for entry in radio.entries:
            row = rows.pop(entry.position, None)
            if row is None:
                row = RadioEpisodeTrack(
                    episode_id=episode.id,
                    position=entry.position,
                    artist_text=entry.artist_text,
                    title_text=entry.title_text,
                )
                session.add(row)
            recording = applied.recordings.get(entry.track_key)
            source = applied.sources.get(entry.track_key)
            row.artist_text, row.title_text = entry.artist_text, entry.title_text
            row.recording_id = recording.id if recording is not None else None
            row.source_id = (
                source.id if source is not None and source.kind is SourceKind.slice else None
            )
            row.offset_is_estimate = entry.offset_is_estimate
        for gone in rows.values():  # no longer in NTS's tracklist
            session.delete(gone)
        delete_unused_slices(session, old_slices)
        if dropped := delete_unused_recordings(session, old_recordings):
            ctx.log(f"radio: {dropped} recording(s) no tracklist entry plays any more deleted")
        matched = sum(applied.recordings.get(e.track_key) is not None for e in radio.entries)
        ctx.log(
            f"radio: {show.name!r} / {episode.name!r}, {len(radio.entries)} entries, "
            f"{matched} with a recording"
        )

    def _image(
        self,
        ctx: JobContext,
        kind: str,
        row: RadioShow | RadioEpisode,
        image: IncomingImage | None,
    ) -> None:
        """Store a fetched show or episode image (replacing the previous one)."""
        if image is None or image.path is None:
            return
        try:
            data = Path(image.path).read_bytes()
            new = row.image_path is None
            row.image_path = covers.replace_image(
                kind, row.id, data, row.image_path, ctx.settings.library_dir
            )
        except (OSError, ValueError) as e:
            ctx.log(f"{kind} image not used: {e}", LogLevel.warning)
            return
        if new:
            written = Path(row.image_path)
            ctx.rollback_actions.append(lambda: written.unlink(missing_ok=True))

    def _link_page(self, ctx: JobContext, file: Source, page_url: str) -> None:
        """The source chain ``nts`` (episode page) → ``soundcloud`` → ``file``: the
        SoundCloud link the file came from gets the episode page as its parent."""
        link = file.parent
        if link is None or link.kind is not SourceKind.soundcloud or link.parent_id is not None:
            return
        page = ctx.session.exec(
            select(Source).where(Source.kind == SourceKind.nts, Source.uri == page_url)
        ).first()
        if page is None:
            page = Source(kind=SourceKind.nts, uri=page_url)
            ctx.session.add(page)
        link.parent = page  # the relationship: inserted before the update


__all__ = ["NtsImporter", "time_ranges"]
