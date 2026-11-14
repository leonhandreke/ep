"""Read facts and tags from an audio file: ``ffprobe`` for the stream, ``mutagen`` for tags.

Pure reading: files are opened read-only and never modified (they may live in a
read-only watched directory).
"""

import base64
import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.easymp4 import EasyMP4Tags
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4, MP4Cover

# Picard's MP4 names for tags that mutagen's EasyMP4 does not map by default.
for _key, _name in {
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "isrc": "ISRC",
}.items():
    EasyMP4Tags.RegisterFreeformKey(_key, _name)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_FRONT_COVER = 3  # ID3 / FLAC picture type


class ProbeError(Exception):
    """The file is missing, or not something ffprobe can read as audio."""


@dataclass(frozen=True)
class Cover:
    data: bytes
    mime: str


@dataclass(frozen=True)
class Tags:
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    track_number: int | None = None
    track_total: int | None = None
    disc_number: int | None = None
    disc_total: int | None = None
    # As tagged, typically YYYY or YYYY-MM-DD.
    date: str | None = None
    isrcs: list[str] = field(default_factory=list)
    mb_recording_id: uuid.UUID | None = None
    mb_release_track_id: uuid.UUID | None = None
    mb_release_id: uuid.UUID | None = None
    mb_release_group_id: uuid.UUID | None = None
    mb_artist_ids: list[uuid.UUID] = field(default_factory=list)
    mb_album_artist_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class ProbeResult:
    path: Path
    size: int
    duration_ms: int | None
    # ffprobe codec name of the first audio stream, e.g. flac, mp3, aac, opus.
    codec: str
    # ffprobe container format name, e.g. flac, mp3, "mov,mp4,m4a,3gp,3g2,mj2".
    format: str
    tags: Tags
    cover: Cover | None


def probe(path: Path) -> ProbeResult:
    """Read duration, codec, size, tags and embedded cover of an audio file."""
    if not path.is_file():
        raise ProbeError(f"not a file: {path}")
    info = _ffprobe(path)
    stream = info["streams"][0]
    duration = info["format"].get("duration") or stream.get("duration")
    tags, cover = _read_tags(path)
    return ProbeResult(
        path=path,
        size=path.stat().st_size,
        duration_ms=round(float(duration) * 1000) if duration else None,
        codec=stream["codec_name"],
        format=info["format"]["format_name"],
        tags=tags,
        cover=cover,
    )


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=format_name,duration:stream=codec_name,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    if not info.get("streams"):
        raise ProbeError(f"no audio stream in {path}")
    return info


def _read_tags(path: Path) -> tuple[Tags, Cover | None]:
    try:
        easy = mutagen.File(path, easy=True)
        full = mutagen.File(path)
    except mutagen.MutagenError:
        return Tags(), None
    values = {k.lower(): list(v) for k, v in (easy.tags or {}).items()} if easy else {}

    def first(key: str) -> str | None:
        return next((v.strip() for v in values.get(key, []) if str(v).strip()), None)

    def ids(key: str) -> list[uuid.UUID]:
        return [uuid.UUID(m) for v in values.get(key, []) for m in _UUID.findall(str(v))]

    track_number, track_total = _number_pair(first("tracknumber"))
    disc_number, disc_total = _number_pair(first("discnumber"))
    tags = Tags(
        title=first("title"),
        artist=first("artist"),
        album_artist=first("albumartist"),
        album=first("album"),
        track_number=track_number,
        track_total=track_total or _int(first("tracktotal") or first("totaltracks")),
        disc_number=disc_number,
        disc_total=disc_total or _int(first("disctotal") or first("totaldiscs")),
        date=first("date"),
        isrcs=[v.strip() for v in values.get("isrc", []) if v.strip()],
        mb_recording_id=next(iter(ids("musicbrainz_trackid")), None),
        mb_release_track_id=next(iter(ids("musicbrainz_releasetrackid")), None),
        mb_release_id=next(iter(ids("musicbrainz_albumid")), None),
        mb_release_group_id=next(iter(ids("musicbrainz_releasegroupid")), None),
        mb_artist_ids=ids("musicbrainz_artistid"),
        mb_album_artist_ids=ids("musicbrainz_albumartistid"),
    )
    return tags, _cover(full)


def _number_pair(value: str | None) -> tuple[int | None, int | None]:
    """ "3/12" -> (3, 12); "3" -> (3, None)."""
    if value is None:
        return None, None
    number, _, total = value.partition("/")
    return _int(number), _int(total)


def _int(value: str | None) -> int | None:
    try:
        return int(value.strip()) if value else None
    except ValueError:
        return None


def _cover(file: mutagen.FileType | None) -> Cover | None:
    """The embedded front cover, or else the first embedded picture."""
    if file is None:
        return None
    if isinstance(file, FLAC):
        pictures = file.pictures
    elif isinstance(file.tags, ID3):
        pictures = file.tags.getall("APIC")
    elif isinstance(file, MP4):
        covr: list[MP4Cover] = (file.tags or {}).get("covr") or []
        if not covr:
            return None
        png = covr[0].imageformat == MP4Cover.FORMAT_PNG
        return Cover(bytes(covr[0]), "image/png" if png else "image/jpeg")
    else:  # Ogg Vorbis / Opus: base64 FLAC picture blocks in the comments
        blocks = (file.tags or {}).get("metadata_block_picture", [])
        pictures = [Picture(base64.b64decode(block)) for block in blocks]
    pictures = sorted(pictures, key=lambda p: p.type != _FRONT_COVER)
    return next((Cover(p.data, p.mime) for p in pictures if p.data), None)
