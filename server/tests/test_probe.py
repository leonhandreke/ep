import hashlib
import uuid
from pathlib import Path

import pytest

from ep.covers import image_extension
from ep.probe import ProbeError, probe
from tests.conftest import MB_ARTIST_IDS, MB_RECORDING_ID, MB_RELEASE_GROUP_ID, MB_RELEASE_ID


def _digest(path: Path) -> tuple[str, float]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime


def test_probe_tagged_flac(audio_dir: Path) -> None:
    path = audio_dir / "album.flac"
    before = _digest(path)
    result = probe(path)
    assert _digest(path) == before  # read only

    assert result.codec == "flac"
    assert result.format == "flac"
    assert result.size == path.stat().st_size
    assert result.duration_ms == pytest.approx(6000, abs=50)
    tags = result.tags
    assert (tags.title, tags.artist, tags.album_artist, tags.album) == (
        "Sinnerman",
        "Nina Simone",
        "Nina Simone",
        "Pastel Blues",
    )
    assert (tags.track_number, tags.track_total) == (3, 12)
    assert (tags.disc_number, tags.disc_total) == (1, 2)
    assert tags.date == "1965-10"
    assert tags.isrcs == ["USPR36500123"]
    assert tags.mb_recording_id == uuid.UUID(MB_RECORDING_ID)
    assert tags.mb_release_id == uuid.UUID(MB_RELEASE_ID)
    assert tags.mb_release_group_id == uuid.UUID(MB_RELEASE_GROUP_ID)
    assert tags.mb_artist_ids == [uuid.UUID(i) for i in MB_ARTIST_IDS]
    assert tags.mb_album_artist_ids == []
    assert result.cover is not None
    assert result.cover.mime == "image/png"
    assert image_extension(result.cover.data) == ".png"


def test_probe_mp3_with_id3_cover(audio_dir: Path) -> None:
    result = probe(audio_dir / "song.mp3")
    assert result.codec == "mp3"
    assert result.duration_ms == pytest.approx(3000, abs=100)
    assert result.tags.title == "Feeling Good"
    assert (result.tags.track_number, result.tags.track_total) == (2, None)
    assert result.cover is not None
    assert image_extension(result.cover.data) == ".png"


def test_probe_m4a_without_cover(audio_dir: Path) -> None:
    result = probe(audio_dir / "song.m4a")
    assert result.codec == "aac"
    assert result.tags.title == "Lilac Wine"
    assert result.tags.artist is None
    assert result.cover is None


def test_probe_opus_with_picture_block(audio_dir: Path) -> None:
    result = probe(audio_dir / "song.opus")
    assert (result.codec, result.format) == ("opus", "ogg")
    assert result.tags.title == "Ne me quitte pas"
    assert result.cover is not None
    assert result.cover.mime == "image/png"
    assert image_extension(result.cover.data) == ".png"


def test_probe_rejects_missing_and_non_audio_files(audio_dir: Path) -> None:
    with pytest.raises(ProbeError):
        probe(audio_dir / "missing.flac")
    with pytest.raises(ProbeError):
        probe(audio_dir / "notes.txt")
    with pytest.raises(ProbeError):
        probe(audio_dir)
