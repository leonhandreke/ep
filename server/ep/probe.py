"""Read facts and tags from an audio file: ``ffprobe`` for the stream, ``mutagen`` for tags.

Pure reading: files are opened read-only and never modified (they may live in a
read-only watched directory).
"""

import base64
import json
import mimetypes
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
from mutagen.ogg import OggFileType

# Picard's MP4 names for tags that mutagen's EasyMP4 does not map by default.
for _key, _name in {
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "isrc": "ISRC",
}.items():
    EasyMP4Tags.RegisterFreeformKey(_key, _name)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_FRONT_COVER = 3  # ID3 / FLAC picture type


# Media types by ffprobe container format name.
_FORMAT_TYPES = {
    "aac": "audio/aac",
    "aiff": "audio/aiff",
    "flac": "audio/flac",
    "mov,mp4,m4a,3gp,3g2,mj2": "audio/mp4",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}

# Media types by file extension, for files not probed. Python's table has e.g.
# audio/mp4a-latm for .m4a and nothing for .opus.
_EXTENSION_TYPES = {
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mka": "audio/x-matroska",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def media_type_of_path(path: Path) -> str:
    """The media type of an audio file going by its extension (a file not probed)."""
    return (
        _EXTENSION_TYPES.get(path.suffix.lower())
        or mimetypes.guess_file_type(path)[0]
        or "application/octet-stream"
    )


def media_type(format_name: str, path: Path) -> str:
    """The media type of a probed file: its container as ffprobe read it; the extension
    only tells WebM from other Matroska (ffprobe names both "matroska,webm") and stands
    in for formats not in the table."""
    if format_name == "matroska,webm":
        return "audio/webm" if path.suffix.lower() == ".webm" else "audio/x-matroska"
    return _FORMAT_TYPES.get(format_name) or media_type_of_path(path)


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
    # The container as a media type (``media_type``), e.g. audio/ogg, audio/webm.
    media_type: str
    tags: Tags
    cover: Cover | None
    # Stream facts (null when the format does not state them): sample rate in Hz, bits
    # per sample (lossless formats), bit rate in bit/s, channel count.
    sample_rate: int | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    channels: int | None = None

    def audio_facts(self) -> dict[str, int | str | None]:
        """The columns of a ``file`` source (and ``IncomingMedium``) read from the file."""
        return {
            "media_type": self.media_type,
            "codec": self.codec,
            "duration_ms": self.duration_ms,
            "size": self.size,
            "sample_rate": self.sample_rate,
            "bit_depth": self.bit_depth,
            "bit_rate": self.bit_rate,
            "channels": self.channels,
        }


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
        media_type=media_type(info["format"]["format_name"], path),
        tags=tags,
        cover=cover,
        sample_rate=_int(stream.get("sample_rate")),
        bit_depth=_int(stream.get("bits_per_raw_sample")) or _int(stream.get("bits_per_sample")),
        bit_rate=_int(stream.get("bit_rate")) or _int(info["format"].get("bit_rate")),
        channels=stream.get("channels"),
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
            "format=format_name,duration,bit_rate:"
            "stream=codec_name,duration,sample_rate,bits_per_raw_sample,bits_per_sample,"
            "bit_rate,channels",
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


def _int(value: str | int | None) -> int | None:
    """A positive integer from a tag or ffprobe value; None for missing, 0 or junk."""
    try:
        number = int(value.strip() if isinstance(value, str) else value) if value else None
    except ValueError:
        return None
    return number or None


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
    elif isinstance(file, OggFileType):  # base64 FLAC picture blocks in the comments
        blocks = file.get("metadata_block_picture", [])
        pictures = [Picture(base64.b64decode(block)) for block in blocks]
    else:
        return None
    pictures = sorted(pictures, key=lambda p: p.type != _FRONT_COVER)
    return next((Cover(p.data, p.mime) for p in pictures if p.data), None)
