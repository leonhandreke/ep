"""Folder import: index audio files in place (``managed=false``), e.g. in a watched dir.

Input: a directory under the media roots. The importer

1. fetches: lists the audio files below it (recursively; hidden entries are ignored),
   leaves out files that are already sources, reads tags with ``ep.probe`` and hashes
   each file (an unreadable file is an ``unreadable_file`` issue, not a crash);
2. groups the files into albums: by tagged MusicBrainz release, else by album tag and
   album artist (or directory), else by directory. A directory with one cue sheet for
   one of its audio files makes that file a single-file album: one ``file`` source and a
   ``slice`` per track;
3. resolves metadata per album: a tagged release MBID is looked up (unknown to
   MusicBrainz: a ``metadata_not_found`` issue); otherwise MusicBrainz is searched by
   album, album artist and track count, and up to ``MAX_RELEASE_LOOKUPS`` candidates
   are fetched to compare track durations. Nothing fitting: the tags are kept as
   entered, which is a normal outcome. Candidates that do not fit but are otherwise
   exact: an ``uncertain_metadata`` issue.

Matching and applying are the framework's (``ep.importers.framework``). The cover is an
image file named cover/folder/front next to the files, else the embedded picture; it is
stored by ``ep.covers`` in the managed library.
"""

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import col, select

from ep.importers.cue import CueError, CueSheet, read_cue
from ep.importers.framework import (
    Importer,
    ImportFailed,
    JobContext,
    answer_fits,
    credit_name,
    release_label,
    sha256_file,
)
from ep.importers.musicbrainz import release_group_type
from ep.importers.proposal import (
    SKIP,
    CandidateType,
    Choice,
    CoverKind,
    IncomingArtist,
    IncomingCover,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    LogLevel,
    Proposal,
    Subject,
    SubjectType,
    Target,
    TargetKind,
    issue_id,
)
from ep.matching.match import DURATION_TOLERANCE_MS
from ep.media_roots import is_within_media_roots
from ep.metadata.musicbrainz import (
    MBArtistCreditPart,
    MBRelease,
    MBTrack,
    MusicBrainzError,
    MusicBrainzNotFound,
    lucene_query,
    render_artist_credit,
)
from ep.models import Artist, ImportKind, JobStage, ReleaseGroup, Source, SourceKind
from ep.probe import ProbeError, ProbeResult, probe

AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aif", ".aiff", ".alac", ".ape", ".flac", ".m4a", ".mp3", ".mp4", ".oga",
     ".ogg", ".opus", ".wav", ".wma", ".wv"}
)  # fmt: skip
COVER_NAMES = ("cover", "folder", "front")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
# MusicBrainz search hits below this relevance (0-100) are not considered.
MIN_SEARCH_SCORE = 90
# Search candidates fetched in full to compare durations (one request each).
MAX_RELEASE_LOOKUPS = 3
# Candidates shown in an uncertain_metadata issue.
MAX_METADATA_CANDIDATES = 5
VARIOUS_ARTISTS = "Various Artists"
UNKNOWN_ARTIST = "Unknown Artist"
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_YEAR = re.compile(r"^(\d{4})\b")


@dataclass
class _Album:
    """Files grouped into one album, before metadata is resolved."""

    key: str
    directory: Path
    files: list[ProbeResult]
    cue: CueSheet | None = None


def audio_files(directory: Path) -> list[Path]:
    """Audio files below ``directory``, sorted; hidden files and directories are skipped."""
    return sorted(
        path
        for path in directory.rglob("*")
        if path.suffix.lower() in AUDIO_EXTENSIONS
        and path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(directory).parts)
    )


class FolderImporter(Importer):
    kind = ImportKind.folder

    def build(self, ctx: JobContext) -> Proposal:
        root = Path(ctx.job.input)
        if not is_within_media_roots(root, ctx.settings.media_roots):
            raise ImportFailed(f"{root} is not under one of the media roots")
        if not root.is_dir():
            raise ImportFailed(f"{root} is not a directory (moved or deleted?)")
        ctx.stage(JobStage.fetch)
        files = audio_files(root)
        if outside := [f for f in files if not is_within_media_roots(f, ctx.settings.media_roots)]:
            ctx.log(f"left out {len(outside)} file(s) linked from outside the media roots")
            files = [f for f in files if f not in outside]
        known = set(
            ctx.session.exec(
                select(Source.uri).where(
                    Source.kind == SourceKind.file, col(Source.uri).in_([str(f) for f in files])
                )
            )
        )
        if known:
            ctx.log(f"{len(known)} of {len(files)} audio file(s) are already in the library")
        new_files = [f for f in files if str(f) not in known]
        if not files:
            ctx.log(f"no audio files in {root}", LogLevel.warning)
        ctx.stage(JobStage.fetch, len(new_files))
        proposal = Proposal()
        probed: list[ProbeResult] = []
        for path in new_files:
            if (result := self._read(ctx, proposal, path)) is not None:
                probed.append(result)
            ctx.advance()

        albums = self._group(ctx, root, probed)
        ctx.stage(JobStage.resolve, len(albums))
        for album in albums:
            release = self._release(proposal, album)
            proposal.releases.append(release)
            self._resolve(ctx, proposal, release, album)
            ctx.advance()
        _prune_artists(proposal)
        return proposal

    # Fetch

    def _read(self, ctx: JobContext, proposal: Proposal, path: Path) -> ProbeResult | None:
        try:
            result = probe(path)
            sha256 = sha256_file(path)
        except (ProbeError, OSError) as e:
            issue = Issue(
                id=issue_id(IssueKind.unreadable_file, str(path)),
                kind=IssueKind.unreadable_file,
                message=f"{path.name} cannot be read as audio: {e}",
                subject=Subject(type=SubjectType.medium, key=str(path), label=str(path)),
                choices=[Choice.skip, Choice.retry],
            )
            if ctx.raise_issue(issue) is not None:
                ctx.log(f"skipped unreadable file {path}")
            return None
        proposal.media.append(
            IncomingMedium(
                key=str(path),
                path=str(path),
                codec=result.codec,
                duration_ms=result.duration_ms,
                size=result.size,
                sha256=sha256,
            )
        )
        return result

    def _group(self, ctx: JobContext, root: Path, probed: list[ProbeResult]) -> list[_Album]:
        albums: list[_Album] = []
        by_path = {result.path: result for result in probed}
        used: set[Path] = set()
        for cue_path in sorted(root.rglob("*.cue"), key=str):
            if any(part.startswith(".") for part in cue_path.relative_to(root).parts):
                continue
            try:
                sheet = read_cue(cue_path)
            except (CueError, OSError) as e:
                ctx.log(f"cue sheet {cue_path} not used: {e}", LogLevel.warning)
                continue
            if len(sheet.files) != 1:
                ctx.log(f"cue sheet {cue_path} lists {len(sheet.files)} files; not used")
                continue
            if sum(path.parent == cue_path.parent for path in by_path) != 1:
                continue  # a cue sheet next to one file per track: the tracks are the files
            audio = _cue_audio(cue_path, sheet.files[0].name, by_path)
            if audio is not None and audio not in used:
                used.add(audio)
                albums.append(_Album(f"cue|{cue_path}", cue_path.parent, [by_path[audio]], sheet))
        groups: dict[str, list[ProbeResult]] = defaultdict(list)
        for result in probed:
            if result.path not in used:
                groups[_group_key(result)].append(result)
        for key, files in groups.items():
            albums.append(_Album(key, files[0].path.parent, files))
        return albums

    # Albums from tags

    def _release(self, proposal: Proposal, album: _Album) -> IncomingRelease:
        tags = [file.tags for file in album.files]
        cue = album.cue
        album_artist = (cue.performer if cue else None) or _common(t.album_artist for t in tags)
        track_artists = {t.artist for t in tags if t.artist}
        if album_artist is None:
            if len(track_artists) == 1:
                album_artist = next(iter(track_artists))
            else:
                album_artist = VARIOUS_ARTISTS if track_artists else UNKNOWN_ARTIST
        album_artist_ids = _common_ids(t.mb_album_artist_ids for t in tags)
        release_credit = [_credit_part(proposal, album_artist, album_artist_ids)]
        title = (cue.title if cue else None) or _common(t.album for t in tags)
        title = title or album.directory.name
        release = IncomingRelease(
            key=album.key,
            title=title,
            credit=release_credit,
            date=_date((cue.date if cue else None) or _common(t.date for t in tags)),
            release_mbid=_common(t.mb_release_id for t in tags),
            mbid=_common(t.mb_release_group_id for t in tags),
            cover=_cover(album),
            tracks=[],
        )
        if cue is not None:
            release.tracks = self._cue_tracks(proposal, album, release_credit)
        else:
            release.tracks = self._file_tracks(proposal, album, album_artist, release_credit)
        return release

    def _file_tracks(
        self,
        proposal: Proposal,
        album: _Album,
        album_artist: str,
        release_credit: list[IncomingCreditPart],
    ) -> list[IncomingTrack]:
        files = sorted(
            album.files,
            key=lambda f: (f.tags.disc_number or 1, f.tags.track_number or 10**6, f.path.name),
        )
        numbers = Counter((f.tags.disc_number or 1, f.tags.track_number) for f in files)
        tracks = []
        for index, file in enumerate(files, start=1):
            tags = file.tags
            disc = tags.disc_number or 1
            numbered = tags.track_number is not None and numbers[(disc, tags.track_number)] == 1
            artist = tags.artist or album_artist
            credit = (
                release_credit
                if artist == album_artist
                else [_credit_part(proposal, artist, tags.mb_artist_ids)]
            )
            tracks.append(
                IncomingTrack(
                    key=str(file.path),
                    medium_key=str(file.path),
                    disc=disc,
                    position=tags.track_number if numbered and tags.track_number else index,
                    title=tags.title or file.path.stem,
                    credit=credit,
                    length_ms=file.duration_ms,
                    isrcs=tags.isrcs,
                    mbid=tags.mb_recording_id,
                    mb_track_id=tags.mb_release_track_id,
                )
            )
        return tracks

    def _cue_tracks(
        self, proposal: Proposal, album: _Album, release_credit: list[IncomingCreditPart]
    ) -> list[IncomingTrack]:
        assert album.cue is not None
        [file] = album.files
        cue_tracks = sorted(album.cue.files[0].tracks, key=lambda t: t.start_ms or 0)
        tracks = []
        for index, cue_track in enumerate(cue_tracks):
            start = cue_track.start_ms or 0
            end = cue_tracks[index + 1].start_ms if index + 1 < len(cue_tracks) else None
            stop = end if end is not None else file.duration_ms
            performer = cue_track.performer
            credit = (
                [_credit_part(proposal, performer, [])]
                if performer and performer != album.cue.performer
                else release_credit
            )
            tracks.append(
                IncomingTrack(
                    key=f"{file.path}#{cue_track.number}",
                    medium_key=str(file.path),
                    start_ms=start,
                    end_ms=end,
                    position=cue_track.number,
                    title=cue_track.title or f"Track {cue_track.number}",
                    credit=credit,
                    length_ms=stop - start if stop is not None else None,
                    isrcs=[cue_track.isrc] if cue_track.isrc else [],
                )
            )
        return tracks

    # Metadata

    def _resolve(
        self, ctx: JobContext, proposal: Proposal, release: IncomingRelease, album: _Album
    ) -> None:
        subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
        if release.release_mbid is not None:
            try:
                mb_release = ctx.mb_release(release.release_mbid)
            except MusicBrainzNotFound:
                issue = Issue(
                    id=issue_id(IssueKind.metadata_not_found, release.key),
                    kind=IssueKind.metadata_not_found,
                    message=(
                        f"The files are tagged with MusicBrainz release "
                        f"{release.release_mbid}, which MusicBrainz does not know (merged "
                        "or deleted?). Import them as tagged, or skip them."
                    ),
                    subject=subject,
                    choices=[Choice.keep_as_entered, Choice.skip, Choice.retry],
                )
                answer = ctx.raise_issue(issue)  # while open, matching waits for it
                release.release_mbid = release.mbid = None
                if answer is not None and answer.choice is Choice.skip:
                    _skip(release)
                return
            except MusicBrainzError as e:
                raise ImportFailed(f"MusicBrainz lookup failed: {e}") from e
            if not _use_musicbrainz(ctx, proposal, release, mb_release):
                release.release_mbid = release.mbid = None
            return
        mb_release = self._search(ctx, release, subject)
        if mb_release is not None:
            _use_musicbrainz(ctx, proposal, release, mb_release)

    def _search(
        self, ctx: JobContext, release: IncomingRelease, subject: Subject
    ) -> MBRelease | None:
        artist = credit_name(release.credit)
        count = len(release.tracks)
        query = lucene_query(
            release=release.title,
            artist=artist if artist != UNKNOWN_ARTIST else None,
            tracks=str(count),
        )
        try:
            hits = ctx.musicbrainz.search_releases(query, limit=25)
        except MusicBrainzError as e:
            raise ImportFailed(f"MusicBrainz search failed: {e}") from e
        known = set(
            ctx.session.exec(
                select(ReleaseGroup.release_mbid).where(
                    col(ReleaseGroup.release_mbid).in_([hit.id for hit in hits])
                )
            )
        )
        candidates = sorted(
            (
                hit
                for hit in hits
                if (hit.score or 0) >= MIN_SEARCH_SCORE
                and sum(m.track_count for m in hit.media) == count
            ),
            key=lambda hit: (
                hit.id in known,
                hit.score or 0,
                any(m.format == "Digital Media" for m in hit.media),
                hit.country == "XW",
                hit.date is not None,
            ),
            reverse=True,
        )
        issue = Issue(
            id=issue_id(IssueKind.uncertain_metadata, release.key),
            kind=IssueKind.uncertain_metadata,
            message=(
                f"MusicBrainz has releases like {release_label(release)!r}, but their track "
                "lengths do not fit the files. Use one, or import the files as tagged."
            ),
            subject=subject,
            candidates=[_mb_candidate(hit) for hit in candidates[:MAX_METADATA_CANDIDATES]],
            choices=[Choice.pick_candidate, Choice.keep_as_entered, Choice.skip],
        )
        answer = ctx.answers.get(issue.id)
        if answer is not None and answer_fits(issue, answer):
            ctx.raise_issue(issue)
            match answer.choice:
                case Choice.pick_candidate:
                    assert answer.candidate_id is not None
                    return ctx.mb_release(answer.candidate_id)
                case Choice.skip:
                    _skip(release)
            return None
        for hit in candidates[:MAX_RELEASE_LOOKUPS]:
            try:
                mb_release = ctx.mb_release(hit.id)
            except MusicBrainzNotFound:
                continue
            except MusicBrainzError as e:
                raise ImportFailed(f"MusicBrainz lookup failed: {e}") from e
            aligned = _align(release, mb_release)
            if aligned is not None and _durations_fit(release, aligned):
                ctx.log(f"found in MusicBrainz: {_mb_label(mb_release)} ({mb_release.id})")
                return mb_release
        if candidates and candidates[0].score == 100:
            ctx.raise_issue(issue)  # open: matching waits for the answer
            return None
        ctx.log(
            f"{release_label(release)!r} not found in MusicBrainz; importing the tags as entered"
        )
        return None


def _skip(release: IncomingRelease) -> None:
    release.target = SKIP
    for track in release.tracks:
        track.target = SKIP


# Using a MusicBrainz release


def _align(release: IncomingRelease, mb_release: MBRelease) -> dict[str, MBTrack] | None:
    """Incoming track key -> MusicBrainz track, or None if the files do not fit the
    release: by tagged release track MBID, else recording MBID, else disc and position."""
    mb_tracks = [track for medium in mb_release.media for track in medium.tracks]
    by_id = {track.id: track for track in mb_tracks}
    recording_counts = Counter(track.recording.id for track in mb_tracks)
    by_recording = {t.recording.id: t for t in mb_tracks if recording_counts[t.recording.id] == 1}
    by_position = {
        (medium.position or index, track.position): track
        for index, medium in enumerate(mb_release.media, start=1)
        for track in medium.tracks
    }
    aligned: dict[str, MBTrack] = {}
    for track in release.tracks:
        mb_track = (
            (by_id.get(track.mb_track_id) if track.mb_track_id else None)
            or (by_recording.get(track.mbid) if track.mbid else None)
            or by_position.get((track.disc, track.position))
        )
        if mb_track is None:
            return None
        aligned[track.key] = mb_track
    if len({t.id for t in aligned.values()}) != len(aligned):
        return None
    return aligned


def _durations_fit(release: IncomingRelease, aligned: dict[str, MBTrack]) -> bool:
    """Every track as long as its MusicBrainz track (where both lengths are known)."""
    for track in release.tracks:
        mb_track = aligned[track.key]
        mb_length = mb_track.length or mb_track.recording.length
        if track.length_ms is None or mb_length is None:
            continue
        if abs(track.length_ms - mb_length) > DURATION_TOLERANCE_MS:
            return False
    return True


def _use_musicbrainz(
    ctx: JobContext, proposal: Proposal, release: IncomingRelease, mb_release: MBRelease
) -> bool:
    """Take the release's metadata from MusicBrainz; False (tags kept) if the files do not
    fit its track list."""
    aligned = _align(release, mb_release)
    if aligned is None or mb_release.release_group is None:
        ctx.log(
            f"the files of {release_label(release)!r} do not fit MusicBrainz release "
            f"{mb_release.id}; importing the tags as entered",
            LogLevel.warning,
        )
        return False
    positions = {
        track.id: (medium.position or index, track.position)
        for index, medium in enumerate(mb_release.media, start=1)
        for track in medium.tracks
    }
    mb_group = mb_release.release_group
    release.title = mb_group.title
    release.credit = _mb_credit(ctx, proposal, mb_group.artist_credit or mb_release.artist_credit)
    release.type = release_group_type(mb_group)
    release.date = mb_group.first_release_date or mb_release.date
    release.barcode = mb_release.barcode
    release.mbid, release.release_mbid = mb_group.id, mb_release.id
    release.target = Target(
        kind=TargetKind.musicbrainz, mbid=mb_group.id, label=_mb_label(mb_release)
    )
    for track in release.tracks:
        mb_track = aligned[track.key]
        track.disc, track.position = positions[mb_track.id]
        track.title = mb_track.title
        track.credit = _mb_credit(
            ctx, proposal, mb_track.artist_credit or mb_track.recording.artist_credit
        )
        track.mbid, track.mb_track_id = mb_track.recording.id, mb_track.id
        track.isrcs = sorted({*track.isrcs, *mb_track.recording.isrcs})
    return True


def _mb_credit(
    ctx: JobContext, proposal: Proposal, parts: list[MBArtistCreditPart]
) -> list[IncomingCreditPart]:
    credit = []
    for part in parts:
        key = f"mbid:{part.artist.id}"
        if not any(artist.key == key for artist in proposal.artists):
            row = ctx.session.exec(select(Artist).where(Artist.mbid == part.artist.id)).first()
            if row is not None:
                target = Target(kind=TargetKind.existing, id=row.id, label=row.name)
            else:
                target = Target(
                    kind=TargetKind.musicbrainz, mbid=part.artist.id, label=part.artist.name
                )
            proposal.artists.append(
                IncomingArtist(
                    key=key,
                    name=part.artist.name,
                    sort_name=part.artist.sort_name,
                    mbid=part.artist.id,
                    target=target,
                )
            )
        credit.append(
            IncomingCreditPart(artist_key=key, credited_name=part.name, join_phrase=part.joinphrase)
        )
    return credit


def _mb_label(mb_release: MBRelease) -> str:
    return f"{mb_release.title} — {render_artist_credit(mb_release.artist_credit)}"


def _mb_candidate(hit: MBRelease) -> IssueCandidate:
    facts = [
        hit.date,
        hit.country,
        ", ".join(sorted({m.format for m in hit.media if m.format})) or None,
        f"{sum(m.track_count for m in hit.media)} tracks",
        hit.disambiguation or None,
    ]
    return IssueCandidate(
        type=CandidateType.musicbrainz_release,
        id=hit.id,
        title=hit.title,
        subtitle=render_artist_credit(hit.artist_credit),
        detail=" · ".join(fact for fact in facts if fact),
        score=float(hit.score or 0),
    )


# Tags


def _group_key(result: ProbeResult) -> str:
    tags = result.tags
    if tags.mb_release_id:
        return f"mb|{tags.mb_release_id}"
    if tags.album:
        artist = tags.album_artist.casefold() if tags.album_artist else str(result.path.parent)
        return f"tags|{artist}|{tags.album.casefold()}"
    return f"dir|{result.path.parent}"


def _common[T](values: Iterable[T | None]) -> T | None:
    """The most common non-empty value (first seen on ties), or None."""
    counts = Counter(value for value in values if value)
    return counts.most_common(1)[0][0] if counts else None


def _common_ids(values: Iterable[list[uuid.UUID]]) -> list[uuid.UUID]:
    counts = Counter(tuple(ids) for ids in values if ids)
    return list(counts.most_common(1)[0][0]) if counts else []


def _credit_part(proposal: Proposal, name: str, mbids: list[uuid.UUID]) -> IncomingCreditPart:
    """One artist for a tag value (credits in tags are not split: "Earth, Wind & Fire")."""
    mbid = mbids[0] if len(mbids) == 1 else None
    key = f"mbid:{mbid}" if mbid else f"name:{name.casefold()}"
    if not any(artist.key == key for artist in proposal.artists):
        proposal.artists.append(IncomingArtist(key=key, name=name, mbid=mbid))
    return IncomingCreditPart(artist_key=key, credited_name=name)


def _date(value: str | None) -> str | None:
    if not value:
        return None
    if _PARTIAL_DATE.match(value):
        return value
    return m.group(1) if (m := _YEAR.match(value)) else None


def _cover(album: _Album) -> IncomingCover | None:
    for path in sorted(album.directory.iterdir(), key=lambda p: p.name):
        if path.stem.casefold() in COVER_NAMES and path.suffix.casefold() in COVER_EXTENSIONS:
            return IncomingCover(kind=CoverKind.file, path=str(path))
    for file in album.files:
        if file.cover is not None:
            return IncomingCover(kind=CoverKind.embedded, medium_key=str(file.path))
    return None


def _cue_audio(cue_path: Path, name: str, probed: dict[Path, ProbeResult]) -> Path | None:
    """The audio file a cue sheet's FILE line names; also when it was converted since
    (``album.wav`` in the sheet, ``album.flac`` on disk)."""
    exact = cue_path.parent / name
    if exact in probed:
        return exact
    stem = Path(name).stem.casefold()
    same_stem = [p for p in probed if p.parent == cue_path.parent and p.stem.casefold() == stem]
    return same_stem[0] if len(same_stem) == 1 else None


def _prune_artists(proposal: Proposal) -> None:
    """Drop incoming artists no credit refers to any more (replaced by MusicBrainz)."""
    used = {
        part.artist_key
        for item in [*proposal.releases, *proposal.all_tracks()]
        for part in item.credit
    }
    proposal.artists = [artist for artist in proposal.artists if artist.key in used]
