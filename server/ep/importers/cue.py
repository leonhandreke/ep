"""Cue sheets: the track list of a single-file album (``album.cue`` next to ``album.flac``).

Only what EP uses is read: album and track titles and performers, the date, ISRCs, and
where each track starts (``INDEX 01``, in minutes:seconds:frames at 75 frames per second).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_LINE = re.compile(r"^\s*(\S+)\s*(.*?)\s*$")
_TIME = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})$")
FRAMES_PER_SECOND = 75


class CueError(Exception):
    """Not a usable cue sheet."""


@dataclass
class CueTrack:
    number: int
    title: str | None = None
    performer: str | None = None
    isrc: str | None = None
    # Start of INDEX 01 within the file.
    start_ms: int | None = None


@dataclass
class CueFile:
    name: str
    tracks: list[CueTrack] = field(default_factory=list)


@dataclass
class CueSheet:
    title: str | None = None
    performer: str | None = None
    date: str | None = None
    files: list[CueFile] = field(default_factory=list)


def read_cue(path: Path) -> CueSheet:
    """Parse a cue sheet file (UTF-8, with or without BOM, else Windows-1252)."""
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return parse_cue(text)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _time_ms(value: str) -> int:
    if not (m := _TIME.match(value)):
        raise CueError(f"bad INDEX time {value!r}")
    minutes, seconds, frames = (int(g) for g in m.groups())
    return (minutes * 60 + seconds) * 1000 + round(frames * 1000 / FRAMES_PER_SECOND)


def parse_cue(text: str) -> CueSheet:
    sheet = CueSheet()
    track: CueTrack | None = None
    for line in text.splitlines():
        if not (m := _LINE.match(line)):
            continue
        command, rest = m.group(1).upper(), m.group(2)
        match command:
            case "FILE":
                # FILE "name with spaces.flac" WAVE: the type is the last word.
                name = rest.rsplit(" ", 1)[0] if not rest.endswith('"') else rest
                sheet.files.append(CueFile(_unquote(name.strip())))
                track = None
            case "TRACK":
                if not sheet.files:
                    raise CueError("TRACK before FILE")
                number = rest.split()[0]
                if not number.isdigit():
                    raise CueError(f"bad TRACK number {number!r}")
                track = CueTrack(int(number))
                sheet.files[-1].tracks.append(track)
            case "TITLE" | "PERFORMER":
                value = _unquote(rest) or None
                target = track if track is not None else sheet
                setattr(target, command.lower(), value)
            case "ISRC" if track is not None:
                track.isrc = rest.strip() or None
            case "INDEX" if track is not None:
                number, _, time = rest.partition(" ")
                if number.strip() == "01":
                    track.start_ms = _time_ms(time.strip())
            case "REM":
                key, _, value = rest.partition(" ")
                if key.upper() == "DATE" and track is None:
                    sheet.date = _unquote(value.strip()) or None
    for file in sheet.files:
        for cue_track in file.tracks:
            if cue_track.start_ms is None:
                raise CueError(f"track {cue_track.number} has no INDEX 01")
    return sheet
