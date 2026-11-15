import pytest

from ep.importers.cue import CueError, parse_cue

SHEET = """﻿REM GENRE Electronic
REM DATE "2001"
PERFORMER "Album Artist"
TITLE "Album"
FILE "Album Artist - Album.flac" WAVE
  TRACK 01 AUDIO
    TITLE "First"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Second"
    PERFORMER "Guest"
    ISRC GBAAA0100002
    INDEX 00 03:59:70
    INDEX 01 04:00:37
"""


def test_parse_cue() -> None:
    sheet = parse_cue(SHEET.lstrip("﻿"))
    assert (sheet.title, sheet.performer, sheet.date) == ("Album", "Album Artist", "2001")
    [file] = sheet.files
    assert file.name == "Album Artist - Album.flac"
    first, second = file.tracks
    assert (first.number, first.title, first.performer, first.start_ms) == (1, "First", None, 0)
    assert (second.title, second.performer, second.isrc) == ("Second", "Guest", "GBAAA0100002")
    assert second.start_ms == 240_000 + round(37 * 1000 / 75)


def test_unquoted_file_name() -> None:
    sheet = parse_cue("FILE album.wav WAVE\nTRACK 1 AUDIO\nINDEX 01 00:00:00\n")
    assert sheet.files[0].name == "album.wav"


@pytest.mark.parametrize(
    "text",
    [
        "TRACK 01 AUDIO\n",
        'FILE "a.flac" WAVE\nTRACK 01 AUDIO\nTITLE "no index"\n',
        'FILE "a.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 0:xx:00\n',
    ],
)
def test_bad_cue(text: str) -> None:
    with pytest.raises(CueError):
        parse_cue(text)
