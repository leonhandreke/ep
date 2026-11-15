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
   album, album artist and track count, and a few candidates are fetched to compare
   track durations (``ep.importers.resolve``). Nothing fitting: the tags are kept as
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
    credit_name,
    release_label,
    sha256_file,
)
from ep.importers.proposal import (
    Choice,
    EmbeddedCover,
    FileCover,
    IncomingArtist,
    IncomingCover,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueKind,
    LogLevel,
    Proposal,
    Subject,
    SubjectType,
    issue_id,
)
from ep.importers.resolve import prune_artists, search_release, skip_release, use_musicbrainz
from ep.media_roots import is_within_media_roots
from ep.metadata.musicbrainz import MusicBrainzError, MusicBrainzNotFound
from ep.models import ImportKind, JobStage, ReleaseGroupType, Source, SourceKind
from ep.probe import ProbeError, ProbeResult, probe

AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aif", ".aiff", ".alac", ".ape", ".flac", ".m4a", ".mp3", ".mp4", ".oga",
     ".ogg", ".opus", ".wav", ".wma", ".wv"}
)  # fmt: skip
COVER_NAMES = ("cover", "folder", "front")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
VARIOUS_ARTISTS = "Various Artists"
# MusicBrainz's special purpose artist for compilations of several artists.
VARIOUS_ARTISTS_MBID = uuid.UUID("89ad4ac3-39f7-470e-963a-56509c546377")
UNKNOWN_ARTIST = "Unknown Artist"
# A folder of one disc of an album ("CD1", "Disc 2"): part of the folder above it.
_DISC_FOLDER = re.compile(r"^(?:cd|disc|disk)\s*(\d+)$", re.IGNORECASE)
# A file name that starts with a track number: "01 Title", "1-02 - Title", "03. Title".
_NUMBERED_NAME = re.compile(r"^(?:(\d{1,2})-)?(\d{1,3})(?:\s*[-._]\s*|\s+)(\S.*)$")
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_YEAR = re.compile(r"^(\d{4})\b")


@dataclass
class _Album:
    """Files grouped into one album, before metadata is resolved."""

    key: str
    directory: Path
    files: list[ProbeResult]
    cue: CueSheet | None = None


def album_folder(path: Path, root: Path | None = None) -> Path:
    """The album folder of an audio file: its directory, or the parent of a disc folder
    ("CD1", "Disc 2"), but not above ``root``."""
    folder = path.parent
    if folder != root and _DISC_FOLDER.match(folder.name):
        folder = folder.parent
    return folder


def _folder_disc(path: Path) -> int | None:
    m = _DISC_FOLDER.match(path.parent.name)
    return int(m.group(1)) if m else None


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
        prune_artists(proposal)
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
                sha256=sha256,
                codec=result.codec,
                duration_ms=result.duration_ms,
                size=result.size,
                sample_rate=result.sample_rate,
                bit_depth=result.bit_depth,
                bit_rate=result.bit_rate,
                channels=result.channels,
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
                groups[_group_key(result, root)].append(result)
        for key, files in groups.items():
            albums.append(_Album(key, album_folder(files[0].path, root), files))
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
        various = album_artist.casefold() == VARIOUS_ARTISTS.casefold()
        if various:
            album_artist_ids = [VARIOUS_ARTISTS_MBID]
        release_credit = [_credit_part(proposal, album_artist, album_artist_ids)]
        title = (cue.title if cue else None) or _common(t.album for t in tags)
        title = title or album.directory.name
        release = IncomingRelease(
            key=album.key,
            title=title,
            credit=release_credit,
            type=ReleaseGroupType.compilation if various else ReleaseGroupType.album,
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
        """Tracks from tags; without tags, disc and number from a disc folder and the
        file name ("CD2/03 Title.flac"), the title from the rest of the name."""
        named = {file.path: _numbered_name(file.path) for file in album.files}

        def disc_of(file: ProbeResult) -> int:
            return file.tags.disc_number or _folder_disc(file.path) or named[file.path][0] or 1

        def number_of(file: ProbeResult) -> int | None:
            return file.tags.track_number or named[file.path][1]

        files = sorted(
            album.files,
            key=lambda f: (disc_of(f), number_of(f) or 10**6, f.path.name),
        )
        numbers = Counter((disc_of(f), number_of(f)) for f in files)
        tracks = []
        for index, file in enumerate(files, start=1):
            tags = file.tags
            disc, number = disc_of(file), number_of(file)
            numbered = number is not None and numbers[(disc, number)] == 1
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
                    position=number if numbered and number else index,
                    title=tags.title or named[file.path][2],
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
                    skip_release(release)
                return
            except MusicBrainzError as e:
                raise ImportFailed(f"MusicBrainz lookup failed: {e}") from e
            if not use_musicbrainz(ctx, proposal, release, mb_release):
                release.release_mbid = release.mbid = None
            return
        artist = credit_name(release.credit)
        mb_release = search_release(
            ctx, release, subject, artist=artist if artist != UNKNOWN_ARTIST else None
        )
        if mb_release is not None:
            use_musicbrainz(ctx, proposal, release, mb_release)


# Tags


def _group_key(result: ProbeResult, root: Path) -> str:
    tags = result.tags
    folder = album_folder(result.path, root)
    if tags.mb_release_id:
        return f"mb|{tags.mb_release_id}"
    if tags.album:
        artist = tags.album_artist.casefold() if tags.album_artist else str(folder)
        return f"tags|{artist}|{tags.album.casefold()}"
    return f"dir|{folder}"


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
    """An image file named cover/folder/front in the album folder (or, for a multi-disc
    album, in a disc folder), else the first embedded picture."""
    folders = [album.directory, *sorted({file.path.parent for file in album.files})]
    for folder in dict.fromkeys(folders):
        for path in sorted(folder.iterdir(), key=lambda p: p.name):
            name, suffix = path.stem.casefold(), path.suffix.casefold()
            if name in COVER_NAMES and suffix in COVER_EXTENSIONS and path.is_file():
                return FileCover(path=str(path))
    for file in album.files:
        if file.cover is not None:
            return EmbeddedCover(medium_key=str(file.path))
    return None


def _numbered_name(path: Path) -> tuple[int | None, int | None, str]:
    """Disc, track number and title from a file name: "1-02 Title" -> (1, 2, "Title")."""
    if m := _NUMBERED_NAME.match(path.stem):
        disc, number, title = m.groups()
        return (int(disc) if disc else None), int(number), title
    return None, None, path.stem


def _cue_audio(cue_path: Path, name: str, probed: dict[Path, ProbeResult]) -> Path | None:
    """The audio file a cue sheet's FILE line names; also when it was converted since
    (``album.wav`` in the sheet, ``album.flac`` on disk)."""
    exact = cue_path.parent / name
    if exact in probed:
        return exact
    stem = Path(name).stem.casefold()
    same_stem = [p for p in probed if p.parent == cue_path.parent and p.stem.casefold() == stem]
    return same_stem[0] if len(same_stem) == 1 else None
