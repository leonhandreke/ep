"""Folder import: index audio files in place (``managed=false``), e.g. in a watched dir.

Input: a directory under the media roots. The importer

1. fetches: lists the audio files below it (recursively; hidden entries are ignored),
   leaves out files that are already sources, reads tags with ``ep.probe`` and hashes
   each file (an unreadable file is an ``unreadable_file`` issue, not a crash; it does
   not hold up the job: the other files are imported, and the issue stays in the inbox
   until it is answered: retry reads the file again, skip leaves it out);
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
image file named cover/folder/front next to the files (or in an artwork subfolder:
"Artwork/Front.png"), else the only image file in the album folder, else the embedded
picture, else (for an album found in MusicBrainz) the release's front cover from the
Cover Art Archive; it is stored by ``ep.covers`` in the managed library.

Re-importing a folder also completes the albums already imported from it (their files
are not imported again):

- one without a MusicBrainz release is read and resolved again like a new album (the
  search may have improved, or MusicBrainz gained the release), without raising issues:
  only a release that fits is used. Its release group and recordings gain the MBIDs
  (title and credits stay as they are), each track pinned to the recording its file
  already plays; an album whose files no longer line up with its tracks, or whose
  release is in the library as another release group, is left as it is (logged);
- one without a cover gets one, found the same way as for a new album.
"""

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx2
from sqlalchemy import union
from sqlmodel import Session, col, func, select

from ep import covers
from ep.importers.cue import CueError, CueSheet, read_cue
from ep.importers.framework import (
    Applied,
    Importer,
    ImportFailed,
    JobContext,
    credit_name,
    existing,
    fetch_image,
    release_label,
    sha256_file,
)
from ep.importers.musicbrainz import fill_latin_names_of
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
    UrlCover,
    issue_id,
)
from ep.importers.resolve import prune_artists, search_release, skip_release, use_musicbrainz
from ep.media_roots import is_within_media_roots
from ep.metadata import web
from ep.metadata.musicbrainz import COVER_ART_URL, MusicBrainzError, MusicBrainzNotFound
from ep.models import (
    Artist,
    ArtistCreditName,
    ImportKind,
    JobStage,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Source,
    SourceKind,
    Track,
)
from ep.probe import ProbeError, ProbeResult, probe

AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aif", ".aiff", ".alac", ".ape", ".flac", ".m4a", ".mp3", ".mp4", ".oga",
     ".ogg", ".opus", ".wav", ".wma", ".wv"}
)  # fmt: skip
COVER_NAMES = ("cover", "folder", "front")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
# Subfolders of an album folder that hold its artwork ("Artwork/Front.png").
ARTWORK_FOLDERS = ("artwork", "art", "covers", "scans", "images")
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

    def __init__(self, transport: httpx2.BaseTransport | None = None) -> None:
        self.transport = transport  # for Cover Art Archive covers (tests)

    def fetch_cover(self, url: str) -> bytes:
        return web.fetch(url, self.transport)

    def apply(self, ctx: JobContext, proposal: Proposal, applied: Applied) -> None:
        """Albums imported from this folder before that have no cover get one, and
        their artists still named in another script by MusicBrainz their Latin-script
        name (``ep.importers.musicbrainz.fill_latin_names_of``)."""
        root = Path(ctx.job.input)
        done = {release_group.id for release_group in applied.release_groups.values()}
        albums = _imported_albums(ctx.session, root)
        recordings = {r for _, album_recordings in albums.values() for r in album_recordings}
        fill_latin_names_of(
            _unnamed_artists(ctx.session, recordings),
            ctx.musicbrainz,
            ctx.log,
            ctx.mb_aliases_read,
        )
        for folder, (files, recordings) in albums.items():
            release_group = _album_release_group(ctx.session, recordings)
            if release_group is None or release_group.cover_path or release_group.id in done:
                continue
            done.add(release_group.id)
            data = self._existing_album_cover(ctx, release_group, folder, files)
            if data is None:
                continue
            try:
                covers.set_release_group_cover(release_group, data, ctx.settings.library_dir)
            except ValueError as e:
                ctx.log(f"cover not used: {e}", LogLevel.warning)
                continue
            written = Path(release_group.cover_path or "")
            ctx.rollback_actions.append(lambda written=written: written.unlink(missing_ok=True))
            ctx.log(f"cover added to {release_group.title}")

    def _existing_album_cover(
        self, ctx: JobContext, release_group: ReleaseGroup, folder: Path, files: list[Path]
    ) -> bytes | None:
        """A cover for an album already in the library, as ``_cover`` and ``build`` find
        one for a new album."""
        try:
            if (image := _cover_file(folder, files)) is not None:
                return image.read_bytes()
            for path in files:
                if (found := probe(path).cover) is not None:
                    return found.data
        except (OSError, ProbeError) as e:
            ctx.log(f"cover not read: {e}", LogLevel.warning)
            return None
        if release_group.release_mbid is None:
            return None
        path = fetch_image(ctx, self, cover_art_url(release_group.release_mbid))
        return path.read_bytes() if path else None

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
        unlinked = _unlinked_albums(ctx.session, root) if known else {}
        if unlinked:
            ctx.log(
                f"{len(unlinked)} album(s) imported before have no MusicBrainz release; "
                "looking them up again"
            )
        ctx.stage(JobStage.fetch, len(new_files) + sum(len(f) for f in unlinked.values()))
        proposal = Proposal()
        probed: list[ProbeResult] = []
        for path in new_files:
            if (result := self._read(ctx, proposal, path)) is not None:
                probed.append(result)
            ctx.advance()
        reread: list[tuple[ReleaseGroup, list[Source], list[ProbeResult]]] = []
        for release_group, sources in unlinked.values():
            results = [self._reread(ctx, source) for source in sources]
            ctx.advance(len(sources))
            if all(results):
                reread.append((release_group, sources, [r for r in results if r is not None]))

        albums = self._group(ctx, root, probed)
        ctx.stage(JobStage.resolve, len(albums) + len(reread))
        for album in albums:
            release = self._release(proposal, album)
            proposal.releases.append(release)
            self._resolve(ctx, proposal, release, album)
            self._url_cover(release)
            ctx.advance()
        for release_group, sources, results in reread:
            self._relink(ctx, proposal, root, release_group, sources, results)
            ctx.advance()
        prune_artists(proposal)
        return proposal

    @staticmethod
    def _url_cover(release: IncomingRelease) -> None:
        if release.cover is None and release.release_mbid is not None:
            release.cover = UrlCover(url=cover_art_url(release.release_mbid))

    # Albums imported before

    def _reread(self, ctx: JobContext, source: Source) -> ProbeResult | None:
        """Tags of a file imported before (not hashed again); None if unreadable."""
        try:
            return probe(Path(source.uri or ""))
        except (ProbeError, OSError) as e:
            ctx.log(f"{source.uri} cannot be read again: {e}", LogLevel.warning)
            return None

    def _relink(
        self,
        ctx: JobContext,
        proposal: Proposal,
        root: Path,
        release_group: ReleaseGroup,
        sources: list[Source],
        results: list[ProbeResult],
    ) -> None:
        """Resolve an album imported before without MusicBrainz release again; if a
        release fits, add it to the proposal for the release group it is in, each track
        pinned to the recording its file (or slice) plays."""
        albums = self._group(ctx, root, results)
        if len(albums) != 1:
            ctx.log(f"{release_group.title}: its files are not one album now; left as it is")
            return
        [album] = albums
        release = self._release(proposal, album)
        # Apart from a new album of the same tags in the folder.
        release.key = f"{album.key}|{release_group.id}"
        self._resolve(ctx, proposal, release, album, ask=False)
        if release.release_mbid is None:
            return
        if (
            taken := ctx.session.exec(
                select(ReleaseGroup).where(
                    ReleaseGroup.mbid == release.mbid, ReleaseGroup.id != release_group.id
                )
            ).first()
        ) is not None:
            ctx.log(
                f"{release_group.title}: MusicBrainz release {release.release_mbid} is in the "
                f"library as {taken.title!r} already; left as it is",
                LogLevel.warning,
            )
            return
        played = _played_recordings(sources)
        at_position = {(t.disc, t.position): t.recording_id for t in release_group.tracks}
        for track in release.tracks:
            assert track.medium_key is not None
            recording_id = played.get((track.medium_key, track.start_ms or 0))
            if recording_id is None or at_position.get((track.disc, track.position)) != (
                recording_id
            ):
                ctx.log(
                    f"{release_group.title}: the tracks of MusicBrainz release "
                    f"{release.release_mbid} do not line up with the album's; left as it is",
                    LogLevel.warning,
                )
                return
            track.target = existing(ctx.session.get_one(Recording, recording_id))
        release.target = existing(release_group)
        self._url_cover(release)
        proposal.releases.append(release)
        by_uri = {source.uri: source for source in sources}
        for result in results:
            source = by_uri[str(result.path)]
            proposal.media.append(
                IncomingMedium(
                    key=str(result.path),
                    path=str(result.path),
                    sha256=source.sha256,
                    media_type=result.media_type,
                    codec=result.codec,
                    duration_ms=result.duration_ms,
                    size=result.size,
                    sample_rate=result.sample_rate,
                    bit_depth=result.bit_depth,
                    bit_rate=result.bit_rate,
                    channels=result.channels,
                )
            )

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
                blocking=False,
            )
            if ctx.raise_issue(issue) is not None:
                ctx.log(f"skipped unreadable file {path}")
            return None
        proposal.media.append(
            IncomingMedium(
                key=str(path),
                path=str(path),
                sha256=sha256,
                media_type=result.media_type,
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
        self,
        ctx: JobContext,
        proposal: Proposal,
        release: IncomingRelease,
        album: _Album,
        *,
        ask: bool = True,
    ) -> None:
        """``ask``: False raises no issues (an album already in the library)."""
        subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
        if release.release_mbid is not None:
            try:
                mb_release = ctx.mb_release(release.release_mbid)
            except MusicBrainzNotFound:
                if not ask:
                    release.release_mbid = release.mbid = None
                    return
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
            ctx, release, subject, artist=artist if artist != UNKNOWN_ARTIST else None, ask=ask
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


def cover_art_url(release_mbid: uuid.UUID) -> str:
    """The Cover Art Archive's front cover of a release (1200 px wide)."""
    return f"{COVER_ART_URL}/release/{release_mbid}/front-1200"


def _images(folder: Path) -> list[Path]:
    """The image files directly in ``folder`` (not hidden), by name."""
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.suffix.casefold() in COVER_EXTENSIONS
            and not path.name.startswith(".")
            and path.is_file()
        ),
        key=lambda p: p.name,
    )


def _cover_file(directory: Path, files: Iterable[Path]) -> Path | None:
    """An image file named cover/folder/front in the album folder (or, for a multi-disc
    album, in a disc folder, or in an artwork subfolder: "Artwork/Front.png"), else the
    only image file in the album folder (other scans in subfolders do not count)."""
    artwork = sorted(
        (
            path
            for path in directory.iterdir()
            if path.name.casefold() in ARTWORK_FOLDERS and path.is_dir()
        ),
        key=lambda p: p.name,
    )
    folders = [directory, *sorted({path.parent for path in files}), *artwork]
    for folder in dict.fromkeys(folders):
        for path in _images(folder):
            if path.stem.casefold() in COVER_NAMES:
                return path
    images = _images(directory)
    return images[0] if len(images) == 1 else None


def _cover(album: _Album) -> IncomingCover | None:
    """``_cover_file``, else the first embedded picture."""
    if (path := _cover_file(album.directory, (file.path for file in album.files))) is not None:
        return FileCover(path=str(path))
    for file in album.files:
        if file.cover is not None:
            return EmbeddedCover(medium_key=str(file.path))
    return None


def _imported_albums(session: Session, root: Path) -> dict[Path, tuple[list[Path], set[uuid.UUID]]]:
    """The files below ``root`` that are file sources, by album folder: the files that
    still exist, and the recordings they play (themselves, or as slices of a cue sheet)."""
    sources = session.exec(
        select(Source).where(
            Source.kind == SourceKind.file,
            col(Source.uri).startswith(f"{root}/", autoescape=True),
        )
    ).all()
    slices = session.exec(
        select(Source.parent_id, Source.recording_id).where(
            col(Source.parent_id).in_([source.id for source in sources]),
            col(Source.recording_id).is_not(None),
        )
    ).all()
    sliced: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for parent_id, recording_id in slices:
        if parent_id is not None and recording_id is not None:
            sliced[parent_id].add(recording_id)
    albums: dict[Path, tuple[list[Path], set[uuid.UUID]]] = {}
    for source in sorted(sources, key=lambda source: source.uri or ""):
        path = Path(source.uri or "")
        files, recordings = albums.setdefault(album_folder(path, root), ([], set()))
        if path.is_file():
            files.append(path)
        if source.recording_id is not None:
            recordings.add(source.recording_id)
        recordings |= sliced[source.id]
    return albums


def _unlinked_albums(
    session: Session, root: Path
) -> dict[uuid.UUID, tuple[ReleaseGroup, list[Source]]]:
    """Albums imported from below ``root`` without a MusicBrainz release, with the file
    sources (their slices loaded) of their album folder; only folders whose files all
    still exist."""
    albums: dict[uuid.UUID, tuple[ReleaseGroup, list[Source]]] = {}
    for files, recordings in _imported_albums(session, root).values():
        release_group = _album_release_group(session, recordings)
        if release_group is None or release_group.release_mbid is not None:
            continue
        if release_group.id in albums:
            continue  # files of one album in two folders: not one album folder
        sources = session.exec(
            select(Source)
            .where(Source.kind == SourceKind.file, col(Source.uri).in_([str(f) for f in files]))
            .order_by(col(Source.uri))
        ).all()
        if sources and len(sources) == len(files):
            albums[release_group.id] = (release_group, list(sources))
    return albums


def _played_recordings(sources: Iterable[Source]) -> dict[tuple[str, int], uuid.UUID]:
    """(file path, start ms) -> the recording played there: by a whole file (start 0),
    or by a slice of it."""
    played: dict[tuple[str, int], uuid.UUID] = {}
    for source in sources:
        if source.recording_id is not None:
            played[(source.uri or "", 0)] = source.recording_id
        for child in source.children:
            if child.kind is SourceKind.slice and child.recording_id is not None:
                played[(source.uri or "", child.start_ms or 0)] = child.recording_id
    return played


def _unnamed_artists(session: Session, recordings: set[uuid.UUID]) -> list[Artist]:
    """The artists with an MBID and no original name credited on these recordings or
    on a release group with a track of one (``fill_latin_names_of`` checks the rest)."""
    if not recordings:
        return []
    release_groups = select(Track.release_group_id).where(col(Track.recording_id).in_(recordings))
    credits = union(
        select(Recording.artist_credit_id).where(col(Recording.id).in_(recordings)),
        select(ReleaseGroup.artist_credit_id).where(col(ReleaseGroup.id).in_(release_groups)),
    )
    credited = select(ArtistCreditName.artist_id).where(
        col(ArtistCreditName.artist_credit_id).in_(credits)
    )
    return list(
        session.exec(
            select(Artist).where(
                col(Artist.id).in_(credited),
                col(Artist.mbid).is_not(None),
                col(Artist.original_name).is_(None),
            )
        )
    )


def _album_release_group(session: Session, recordings: set[uuid.UUID]) -> ReleaseGroup | None:
    """The release group with the most tracks of these recordings (None on a tie: an
    album and a compilation of its tracks cannot be told apart)."""
    if not recordings:
        return None
    count = func.count().label("count")
    rows = session.exec(
        select(Track.release_group_id, count)
        .where(col(Track.recording_id).in_(recordings))
        .group_by(col(Track.release_group_id))
        .order_by(count.desc())
        .limit(2)
    ).all()
    if not rows or (len(rows) == 2 and rows[0][1] == rows[1][1]):
        return None
    return session.get(ReleaseGroup, rows[0][0])


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
