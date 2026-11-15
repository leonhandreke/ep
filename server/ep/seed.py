"""A small, fixed demo library for development and the app's screenshot harness.

    uv run python -m ep.seed --database-url postgresql+psycopg://ep:ep@localhost:5432/ep_demo \\
        --library-dir /tmp/ep-demo/library

Migrates the database to head, refuses to touch one that has any library data or
jobs, and refuses a library directory that is not empty or lies under ``~/Music``. It
writes generated audio (ffmpeg sine tones, must be on the ``PATH``) and flat-colour
covers into the library directory, and folders for the watched directories into the
watch directory (``--watch-dir``, default ``<library dir>/../watch``). Run the server
with ``EP_LIBRARY_DIR`` set to the library directory and ``EP_MEDIA_ROOTS`` to both
(the command prints them). Do not run a worker against it: the demo jobs are rows
only, with nothing in the queue (a worker would re-run the "running" one).

Every state the app draws is in it: artists with accented names, one known only from
radio; albums, an EP, a single and a compilation with covers; a multi-disc album; loose
tracks; a single-file album played by slices; tracks that are link-only, without a
source, or whose file is missing; a radio show with two episodes (exact and estimated
times; entries matched to an album track, to a loose track, radio-only and unmatched);
notes with links between artists, albums, tracks and episodes (one broken); tags; and
import jobs running, waiting, needing review, failed and done.

Deterministic: IDs are UUID 5 of fixed names, timestamps fixed (the audio files are
regenerated, so their bytes depend on the ffmpeg version).
"""

import argparse
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, text
from sqlmodel import Session, select

from ep import covers
from ep.importers.proposal import (
    Answer,
    CandidateType,
    Choice,
    ExistingTarget,
    IncomingArtist,
    IncomingCreditPart,
    IncomingImage,
    IncomingMedium,
    IncomingRadio,
    IncomingRadioEntry,
    IncomingRadioEpisode,
    IncomingRadioShow,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    LogEntry,
    LogLevel,
    Origin,
    Proposal,
    Subject,
    SubjectType,
    UrlCover,
)
from ep.models import (
    Artist,
    ArtistCredit,
    ArtistCreditName,
    ArtistType,
    EntityTag,
    EntityType,
    ImportJob,
    ImportKind,
    JobStage,
    JobState,
    Note,
    RadioEpisode,
    RadioEpisodeTrack,
    RadioShow,
    RadioStation,
    Recording,
    ReleaseGroup,
    ReleaseGroupType,
    Source,
    SourceKind,
    Tag,
    Track,
    WatchDir,
)
from ep.notes.links import format_link
from ep.notes.store import save_note

SERVER_DIR = Path(__file__).resolve().parent.parent
NAMESPACE = uuid.UUID("5d0c3a1e-8a51-4c1e-9a43-6f1d2b7e0e11")
BASE_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
# Tables the seed fills; "empty" means all of these are.
TABLES = (
    "artist",
    "release_group",
    "recording",
    "source",
    "radio_show",
    "note",
    "tag",
    "import_job",
    "watch_dir",
)


class SeedError(Exception):
    pass


def det(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "/".join(parts))


def _ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def tone(path: Path, seconds: float, frequency: int) -> Path:
    """A sine tone; the codec follows the extension (mp3, flac, m4a)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = {".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"], ".m4a": ["-c:a", "aac"]}
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={seconds}:sample_rate=44100",
        *codec.get(path.suffix, []),
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        path,
    )
    return path


def colour_png(colour: str) -> bytes:
    """A 300 x 300 PNG of one colour (``#rrggbb``)."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{colour.lstrip('#')}:s=300x300",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


class Seeder:
    def __init__(self, session: Session, library: Path, watch: Path) -> None:
        self.session = session
        self.library = library
        self.watch = watch
        self.artists: dict[str, Artist] = {}
        self.recordings: dict[str, Recording] = {}
        self.release_groups: dict[str, ReleaseGroup] = {}
        self.credits: dict[str, ArtistCredit] = {}
        self.frequency = 220

    # Catalogue

    def artist(
        self,
        name: str,
        sort_name: str | None = None,
        disambiguation: str = "",
        type: ArtistType | None = None,
    ) -> Artist:
        key = f"{name}|{disambiguation}"
        artist = Artist(
            id=det("artist", key),
            name=name,
            sort_name=sort_name or name,
            disambiguation=disambiguation,
            type=type,
        )
        self.session.add(artist)
        self.artists[key] = artist
        return artist

    def credit(self, *parts: tuple[Artist, str]) -> ArtistCredit:
        """A credit of (artist, join phrase) parts."""
        name = "".join(artist.name + join for artist, join in parts)
        if name in self.credits:
            return self.credits[name]
        credit = ArtistCredit(id=det("credit", name), name=name)
        credit.names = [
            ArtistCreditName(
                id=det("credit", name, str(position)),
                artist_credit_id=credit.id,
                artist_id=artist.id,
                position=position,
                credited_name=artist.name,
                join_phrase=join,
            )
            for position, (artist, join) in enumerate(parts)
        ]
        self.session.add(credit)
        self.credits[name] = credit
        return credit

    def release_group(
        self,
        title: str,
        credit: ArtistCredit,
        type: ReleaseGroupType,
        date: str,
        colour: str | None,
    ) -> ReleaseGroup:
        release_group = ReleaseGroup(
            id=det("release_group", title),
            title=title,
            artist_credit_id=credit.id,
            type=type,
            date=date,
        )
        if colour is not None:
            covers.set_release_group_cover(release_group, colour_png(colour), self.library)
        self.session.add(release_group)
        self.release_groups[title] = release_group
        return release_group

    def recording(self, title: str, credit: ArtistCredit, length_ms: int = 4000) -> Recording:
        recording = Recording(
            id=det("recording", title), title=title, artist_credit_id=credit.id, length_ms=length_ms
        )
        self.session.add(recording)
        # Before its sources: recording and source refer to each other, so the unit of
        # work cannot order their inserts by itself.
        self.session.flush()
        self.recordings[title] = recording
        return recording

    def track(
        self, release_group: ReleaseGroup, recording: Recording, position: int, disc: int = 1
    ) -> Track:
        track = Track(
            id=det("track", release_group.title, str(disc), str(position)),
            release_group_id=release_group.id,
            recording_id=recording.id,
            disc=disc,
            position=position,
        )
        self.session.add(track)
        return track

    def file(
        self,
        path: Path,
        seconds: float,
        recording: Recording | None = None,
        parent: Source | None = None,
        write: bool = True,
    ) -> Source:
        """A file source (generated unless ``write`` is false: a missing file)."""
        self.frequency += 55
        if write:
            tone(path, seconds, self.frequency)
        codec = {".mp3": "mp3", ".flac": "flac", ".m4a": "aac"}[path.suffix]
        source = Source(
            id=det("source", str(path.relative_to(self.library.parent))),
            kind=SourceKind.file,
            uri=str(path),
            recording_id=recording.id if recording else None,
            parent_id=parent.id if parent else None,
            managed=path.is_relative_to(self.library),
            codec=codec,
            duration_ms=round(seconds * 1000),
            size=path.stat().st_size if write else None,
            sample_rate=44100,
            bit_depth=16 if codec == "flac" else None,
            bit_rate={"mp3": 192000, "aac": 128000}.get(codec),
            channels=1,
        )
        self.session.add(source)
        return source

    def link(self, kind: SourceKind, uri: str, recording: Recording | None = None) -> Source:
        source = Source(
            id=det("source", uri),
            kind=kind,
            uri=uri,
            recording_id=recording.id if recording else None,
        )
        self.session.add(source)
        return source

    def slice(
        self, parent: Source, start_ms: int, end_ms: int | None, recording: Recording | None
    ) -> Source:
        source = Source(
            id=det("slice", str(parent.id), str(start_ms)),
            kind=SourceKind.slice,
            parent_id=parent.id,
            start_ms=start_ms,
            end_ms=end_ms,
            recording_id=recording.id if recording else None,
            duration_ms=(end_ms - start_ms) if end_ms else None,
        )
        self.session.add(source)
        return source

    def album_files(
        self, artist: str, release_group: ReleaseGroup, ext: str, *recordings: Recording
    ) -> None:
        folder = self.library / artist / release_group.title
        for number, recording in enumerate(recordings, 1):
            self.file(folder / f"{number:02d} {recording.title}{ext}", 4, recording)

    # Library

    def catalogue(self) -> None:
        bjork = self.artist("Björk", type=ArtistType.person)
        arca = self.artist("Arca", disambiguation="Venezuelan producer", type=ArtistType.person)
        self.artist("Arca", disambiguation="Italian band", type=ArtistType.group)
        sigur = self.artist("Sigur Rós", type=ArtistType.group)
        mark_bell = self.artist("Mark Bell", "Bell, Mark", type=ArtistType.person)
        fennesz = self.artist("Fennesz", type=ArtistType.person)
        glenn = self.artist("Beverly Glenn-Copeland", "Glenn-Copeland, Beverly")
        halo = self.artist("Laurel Halo", "Halo, Laurel", type=ArtistType.person)
        self.artist("Mùm", type=ArtistType.group)  # credited on nothing
        b, a, s, f = (
            self.credit((bjork, "")),
            self.credit((arca, "")),
            self.credit((sigur, "")),
            self.credit((fennesz, "")),
        )
        featuring = self.credit((bjork, " feat. "), (mark_bell, ""))
        self.session.flush()

        # An album in every state: own file, file missing, link only, no source.
        homogenic = self.release_group(
            "Homogenic", b, ReleaseGroupType.album, "1997-09-22", "#6d7f99"
        )
        hunter, joga, unravel, bachelorette, full = (
            self.recording(title, b, length)
            for title, length in [
                ("Hunter", 4000),
                ("Jóga", 4000),
                ("Unravel", 4000),
                ("Bachelorette", 4000),
                ("All Is Full of Love", 4000),
            ]
        )
        for position, recording in enumerate([hunter, joga, unravel, bachelorette, full], 1):
            self.track(homogenic, recording, position)
        folder = self.library / "Björk" / "Homogenic"
        bandcamp = self.link(SourceKind.bandcamp, "https://bjork.bandcamp.com/track/hunter")
        self.file(folder / "01 Hunter.flac", 4, hunter, parent=bandcamp)
        self.file(folder / "02 Jóga.flac", 4, joga)
        self.file(folder / "03 Unravel.flac", 4, unravel, write=False)  # missing on disk
        self.link(
            SourceKind.bandcamp, "https://bjork.bandcamp.com/track/bachelorette", bachelorette
        )
        self.link(SourceKind.youtube, "https://www.youtube.com/watch?v=GSWtPDAN3PE", joga)

        # A single of a track on the album (the same recording).
        single = self.release_group("Jóga", b, ReleaseGroupType.single, "1997-09-15", "#a3b18a")
        self.track(single, joga, 1)

        # Two discs, a track credited differently from the album.
        vespertine = self.release_group(
            "Vespertine", b, ReleaseGroupType.album, "2001-08-27", "#d8d2c4"
        )
        hidden, cocoon, pagan, aurora = (
            self.recording("Hidden Place", b),
            self.recording("Cocoon", b),
            self.recording("Pagan Poetry", b),
            self.recording("Aurora", featuring),
        )
        self.track(vespertine, hidden, 1)
        self.track(vespertine, cocoon, 2)
        self.track(vespertine, pagan, 1, disc=2)
        self.track(vespertine, aurora, 2, disc=2)
        folder = self.library / "Björk" / "Vespertine"
        # Cocoon has no file: it plays only as a radio excerpt ("Strings only").
        for name, recording in [
            ("1-01 Hidden Place.m4a", hidden),
            ("2-01 Pagan Poetry.m4a", pagan),
            ("2-02 Aurora.m4a", aurora),
        ]:
            self.file(folder / name, 4, recording)

        # A single-file album: one file, a slice per track.
        medulla = self.release_group("Medúlla", b, ReleaseGroupType.album, "2004-08-30", "#3f3a36")
        whole = self.file(self.library / "Björk" / "Medúlla" / "Medúlla.flac", 12)
        for position, title in enumerate(
            ["Pleasure Is All Mine", "Show Me Forgiveness", "Where Is the Line"], 1
        ):
            recording = self.recording(title, b)
            self.track(medulla, recording, position)
            start = (position - 1) * 4000
            self.slice(whole, start, start + 4000 if position < 3 else None, recording)

        # Loose tracks: one downloaded from YouTube, one only a link.
        loose = self.recording("Loose Tone", b)
        youtube = self.link(SourceKind.youtube, "https://www.youtube.com/watch?v=-i8dwSGgZjE")
        self.file(self.library / "Björk" / "Loose Tone.m4a", 4, loose, parent=youtube)
        demo = self.recording("Demo 1993", b)
        self.link(SourceKind.soundcloud, "https://soundcloud.com/bjork/demo-1993", demo)

        # An EP, a compilation.
        ep = self.release_group("&&&&&", a, ReleaseGroupType.ep, "2013-07-01", "#c46a9b")
        piel = self.recording("Piel", a)
        self.track(ep, piel, 1)
        self.album_files("Arca", ep, ".mp3", piel)
        agaetis = self.release_group(
            "Ágætis byrjun", s, ReleaseGroupType.album, "1999-06-12", "#b7c9d6"
        )
        svefn = self.recording("Svefn-g-englar", s)
        self.track(agaetis, svefn, 1)
        self.album_files("Sigur Rós", agaetis, ".mp3", svefn)
        heim = self.release_group(
            "Hvarf/Heim", s, ReleaseGroupType.compilation, "2007-11-05", "#7a8b6f"
        )
        self.track(heim, svefn, 1)

        # An album with nothing to play: links and metadata only.
        agora = self.release_group("Agora", f, ReleaseGroupType.album, "2019-03-29", "#2d4a5a")
        for position, title in enumerate(["In My Room", "Rainfall", "Agora", "We Trigger"], 1):
            recording = self.recording(title, f, 6000)
            self.track(agora, recording, position)
            if position < 4:
                self.link(
                    SourceKind.spotify, f"https://open.spotify.com/track/agora{position}", recording
                )

        self.glenn, self.halo = glenn, halo
        self.session.flush()

    def radio(self) -> None:
        show = RadioShow(
            id=det("radio_show", "late-night-tapes"),
            station=RadioStation.nts,
            slug="late-night-tapes",
            name="Late Night Tapes",
            description="Two hours of records for the night shift.",
        )
        show.image_path = str(
            covers.write_image("radio-show", show.id, colour_png("#1f2a44"), self.library)
        )
        self.session.add(show)
        ever_new = self.recording("Ever New", self.credit((self.glenn, "")))
        hour_logic = self.recording("Hour Logic", self.credit((self.halo, "")))
        episodes = [
            (
                "homage-to-the-night-shift",
                "Homage to the night shift",
                datetime(2025, 3, 14, 22, tzinfo=UTC),
                "London",
                60,
                False,
                [
                    ("Björk", "Jóga", self.recordings["Jóga"], 5000, 15000),
                    ("Björk", "Loose Tone", self.recordings["Loose Tone"], 15000, 25000),
                    ("Beverly Glenn-Copeland", "Ever New", ever_new, 25000, 35000),
                    ("Laurel Halo", "Hour Logic", hour_logic, 35000, 45000),
                    ("Unknown Artist", "Untitled", None, 45000, None),
                ],
            ),
            (
                "strings-only",
                "Strings only",
                datetime(2024, 11, 24, 22, tzinfo=UTC),
                None,
                40,
                True,
                [
                    ("Björk", "Cocoon", self.recordings["Cocoon"], 5000, 15000),
                    ("Beverly Glenn-Copeland", "Ever New", ever_new, 20000, 30000),
                ],
            ),
        ]
        for slug, name, broadcast_at, location, seconds, estimated, entries in episodes:
            page = self.link(
                SourceKind.nts, f"https://www.nts.live/shows/late-night-tapes/episodes/{slug}"
            )
            path = (
                self.library
                / "_radio"
                / "NTS"
                / "Late Night Tapes"
                / f"{broadcast_at:%Y-%m-%d} {name}.m4a"
            )
            file = self.file(path, seconds, parent=page)
            episode = RadioEpisode(
                id=det("radio_episode", slug),
                show_id=show.id,
                slug=slug,
                name=name,
                description=f"{name}: a demo episode.",
                broadcast_at=broadcast_at,
                location=location,
                url=page.uri or "",
                source_id=file.id,
            )
            episode.image_path = str(
                covers.write_image("radio-episode", episode.id, colour_png("#44385c"), self.library)
            )
            self.session.add(episode)
            for position, (artist_text, title_text, recording, start, end) in enumerate(entries, 1):
                piece = self.slice(file, start, end, recording)
                self.session.add(
                    RadioEpisodeTrack(
                        id=det("radio_episode_track", slug, str(position)),
                        episode_id=episode.id,
                        position=position,
                        artist_text=artist_text,
                        title_text=title_text,
                        recording_id=recording.id if recording else None,
                        source_id=piece.id,
                        offset_is_estimate=estimated,
                    )
                )
        self.show = show
        self.session.flush()

    # Notes and tags

    def notes_and_tags(self) -> None:
        def link(type: EntityType, entity: Artist | ReleaseGroup | Recording | RadioEpisode) -> str:
            title = entity.title if isinstance(entity, (ReleaseGroup, Recording)) else entity.name
            return format_link(type, entity.id, title)

        bjork = self.artists["Björk|"]
        mark_bell = self.artists["Mark Bell|"]
        homogenic, vespertine = self.release_groups["Homogenic"], self.release_groups["Vespertine"]
        joga = self.recordings["Jóga"]
        night = self.session.get_one(
            RadioEpisode, det("radio_episode", "homage-to-the-night-shift")
        )
        broken = format_link(EntityType.artist, det("artist", "The Sugarcubes"), "The Sugarcubes")
        notes = [
            (
                EntityType.artist,
                bjork.id,
                "# Björk\n\n"
                f"Start with {link(EntityType.release_group, homogenic)}, then go sideways to "
                f"{link(EntityType.release_group, vespertine)} for the quiet version of the "
                "same idea.\n\n"
                f"- Collaborators worth following: {link(EntityType.artist, mark_bell)}\n"
                f"- Before all this: {broken} — artist was merged, relink\n"
                f"- Heard on {link(EntityType.radio_episode, night)}\n\n"
                "> Every record sounds like a different room.\n",
            ),
            (
                EntityType.release_group,
                homogenic.id,
                f"Strings and beats. {link(EntityType.recording, joga)} is the centre of it; "
                f"produced with {link(EntityType.artist, mark_bell)}.\n",
            ),
            (
                EntityType.recording,
                joga.id,
                f"Played on {link(EntityType.radio_episode, night)}, talked over at the start.\n",
            ),
            (
                EntityType.artist,
                mark_bell.id,
                f"# Mark Bell\n\nProducer, half of LFO.\n\n### Where he shows up\n\n"
                f"- Co-producer on {link(EntityType.release_group, homogenic)}\n",
            ),
            (
                EntityType.radio_episode,
                night.id,
                f"Into {link(EntityType.artist, self.halo)} near the end.\n",
            ),
        ]
        for entity_type, entity_id, body in notes:
            self.session.add(
                Note(
                    id=det("note", entity_type, str(entity_id)),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    body=body,
                )
            )
            self.session.flush()
            save_note(self.session, entity_type, entity_id, body)
        tags = {
            "strings": [
                (EntityType.release_group, homogenic.id),
                (EntityType.recording, joga.id),
                (EntityType.release_group, vespertine.id),
            ],
            "string-quartet": [(EntityType.recording, self.recordings["Cocoon"].id)],
            "icelandic": [
                (EntityType.artist, bjork.id),
                (EntityType.artist, self.artists["Sigur Rós|"].id),
            ],
            "late night": [
                (EntityType.radio_episode, night.id),
                (EntityType.radio_show, self.show.id),
            ],
        }
        for name, entities in tags.items():
            tag = Tag(id=det("tag", name), name=name)
            self.session.add(tag)
            for entity_type, entity_id in entities:
                self.session.add(
                    EntityTag(
                        id=det("entity_tag", name, str(entity_id)),
                        tag_id=tag.id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                    )
                )
        self.session.flush()

    # Operations

    def watch_dirs(self) -> dict[str, WatchDir]:
        dirs = {}
        for name, enabled in [("Downloads", True), ("incoming", True), ("rips", False)]:
            path = self.watch / name
            path.mkdir(parents=True, exist_ok=True)
            dirs[name] = WatchDir(
                id=det("watch_dir", name), path=str(path), enabled=enabled, baseline_at=BASE_TIME
            )
            self.session.add(dirs[name])
        self.session.flush()
        return dirs

    def jobs(self, dirs: dict[str, WatchDir]) -> None:
        now = BASE_TIME + timedelta(days=13, hours=10)

        def log(minutes: float, *messages: str) -> list[dict]:
            return [
                LogEntry(
                    at=now + timedelta(minutes=minutes, seconds=index),
                    level=LogLevel.info,
                    message=m,
                ).model_dump(mode="json")
                for index, m in enumerate(messages)
            ]

        def job(name: str, kind: ImportKind, input: str, minutes: float, **fields: Any) -> None:
            created = now + timedelta(minutes=minutes)
            row = ImportJob(id=det("import_job", name), kind=kind, input=input, **fields)
            row.created_at = row.updated_at = created
            self.session.add(row)

        def credit(name: str) -> list[IncomingCreditPart]:
            return [IncomingCreditPart(artist_key=name, credited_name=name)]

        # Done: a folder (one new artist), a Bandcamp album.
        job(
            "treasure",
            ImportKind.folder,
            str(self.watch / "Downloads" / "Cocteau Twins - Treasure"),
            -24 * 60,
            state=JobState.done,
            watch_dir_id=dirs["Downloads"].id,
            created={"artist": 1, "release_group": 1, "recording": 10, "track": 10, "source": 10},
            proposal=Proposal(
                artists=[IncomingArtist(key="Cocteau Twins", name="Cocteau Twins")],
                releases=[
                    IncomingRelease(
                        key="treasure", title="Treasure", credit=credit("Cocteau Twins"), tracks=[]
                    )
                ],
            ).model_dump(mode="json"),
            log=log(-24 * 60, "started", "done, created: artist 1, recording 10, track 10"),
        )
        job(
            "untrue",
            ImportKind.bandcamp,
            "https://burial.bandcamp.com/album/untrue",
            -23 * 60,
            state=JobState.done,
            created={"release_group": 1, "recording": 13, "track": 13, "source": 26},
            proposal=Proposal(
                releases=[
                    IncomingRelease(
                        key="untrue", title="Untrue", credit=credit("Burial"), tracks=[]
                    )
                ]
            ).model_dump(mode="json"),
            log=log(-23 * 60, "started", "done, created: recording 13, track 13, source 26"),
        )
        # Failed: an error (retry or dismiss).
        job(
            "shade",
            ImportKind.folder,
            str(self.watch / "Downloads" / "Grouper - Shade"),
            -100,
            state=JobState.failed,
            watch_dir_id=dirs["Downloads"].id,
            error="the folder has no audio files EP can read",
            log=log(-100, "started", "the folder has no audio files EP can read"),
        )
        # Applied in part: two playlist entries failed to download (retry 2).
        playlist_tracks = [
            IncomingTrack(
                key=f"https://www.youtube.com/watch?v=entry{n}",
                medium_key=f"https://www.youtube.com/watch?v=entry{n}",
                position=n,
                title=f"Neighborhood {n}",
                credit=credit("The Dwarfs"),
            )
            for n in range(1, 10)
        ]
        failures = [
            Issue(
                id=f"download_failed:{track.medium_key}",
                kind=IssueKind.download_failed,
                message="download failed: Video unavailable",
                subject=Subject(
                    type=SubjectType.medium, key=track.medium_key or "", label=track.title
                ),
                choices=[Choice.retry, Choice.skip],
                blocking=False,
            )
            for track in playlist_tracks[7:]
        ]
        job(
            "playlist",
            ImportKind.youtube,
            "https://www.youtube.com/playlist?list=PLdemoNeighborhoods",
            -170,
            state=JobState.needs_review,
            created={"release_group": 1, "recording": 7, "track": 7, "source": 14},
            proposal=Proposal(
                media=[
                    IncomingMedium(
                        key=track.medium_key or "",
                        path=None,
                        download=True,
                        managed=True,
                        origin=Origin(kind=SourceKind.youtube, uri=track.medium_key or ""),
                    )
                    for track in playlist_tracks[7:]
                ],
                artists=[IncomingArtist(key="The Dwarfs", name="The Dwarfs")],
                releases=[
                    IncomingRelease(
                        key="playlist",
                        title="Neighborhoods",
                        credit=credit("The Dwarfs"),
                        tracks=playlist_tracks,
                    )
                ],
                issues=failures,
            ).model_dump(mode="json"),
            log=log(
                -170,
                "started",
                "download failed: Video unavailable (2 of 9)",
                "applied; needs review: 2 open issue(s) left out for now",
            ),
        )
        # Needs review: another edition of an album in the library.
        agaetis = self.release_groups["Ágætis byrjun"]
        job(
            "agaetis",
            ImportKind.folder,
            str(self.watch / "incoming" / "Sigur Rós - Ágætis byrjun (2019)"),
            -60,
            state=JobState.needs_review,
            watch_dir_id=dirs["incoming"].id,
            proposal=Proposal(
                artists=[
                    IncomingArtist(
                        key="Sigur Rós",
                        name="Sigur Rós",
                        target=ExistingTarget(id=self.artists["Sigur Rós|"].id, label="Sigur Rós"),
                    )
                ],
                releases=[
                    IncomingRelease(
                        key="agaetis", title="Ágætis byrjun", credit=credit("Sigur Rós"), tracks=[]
                    )
                ],
                issues=[
                    Issue(
                        id="release_conflict:agaetis",
                        kind=IssueKind.release_conflict,
                        message=(
                            "'Ágætis byrjun — Sigur Rós' is already in the library from "
                            "another MusicBrainz release."
                        ),
                        subject=Subject(
                            type=SubjectType.release,
                            key="agaetis",
                            label="Ágætis byrjun — Sigur Rós",
                        ),
                        candidates=[
                            IssueCandidate(
                                type=CandidateType.release_group,
                                id=agaetis.id,
                                title=agaetis.title,
                                subtitle="Sigur Rós",
                                detail=agaetis.date,
                                score=100,
                            )
                        ],
                        choices=[Choice.pick_candidate, Choice.skip],
                    )
                ],
            ).model_dump(mode="json"),
            log=log(-60, "started", "needs review: 1 open issue(s)"),
        )
        # Needs review: a radio episode with uncertain entries (several kinds).
        self._radio_review(job, log)
        # Running (downloading 3 of 5), and one waiting behind it.
        job(
            "agora",
            ImportKind.bandcamp,
            "https://fennesz.bandcamp.com/album/agora-live",
            -1,
            state=JobState.running,
            stage=JobStage.download,
            progress_done=2,
            progress_total=5,
            progress_item=0.62,
            proposal=Proposal(
                releases=[
                    IncomingRelease(
                        key="agora-live",
                        title="Agora (Live)",
                        credit=credit("Fennesz"),
                        cover=UrlCover(url="https://f4.bcbits.com/img/a0000000000_10.jpg"),
                        tracks=[],
                    )
                ]
            ).model_dump(mode="json"),
            log=log(
                -1,
                "started",
                "matched release group “Agora” (MusicBrainz)",
                "artist Fennesz → existing artist (score 0.99)",
                "downloading 03 “We Trigger the Sun” (FLAC)",
            ),
        )
        job(
            "bunny",
            ImportKind.folder,
            str(self.watch / "Downloads" / "Arthur Russell - Picture of Bunny Rabbit"),
            0,
            state=JobState.queued,
            watch_dir_id=dirs["Downloads"].id,
        )
        self.session.flush()

    def _radio_review(self, job: Callable[..., None], log: Callable[..., list[dict]]) -> None:
        url = "https://www.nts.live/shows/late-night-tapes/episodes/turiya-and-friends"
        entries = [
            ("Alice Coltrane", "Turiya and Ramakrishna", 602000, False),
            ("Laurel Halo", "Unknown title", 1095000, False),
            ("Arca", "Piel", 1990000, False),
            ("Jon Hassell", "Dreamworld", 3520000, True),
        ]
        tracks = [
            IncomingTrack(
                key=f"{url}#{n}",
                medium_key=url,
                start_ms=start,
                position=n,
                title=title,
                credit=[IncomingCreditPart(artist_key=artist, credited_name=artist)],
                optional=True,
            )
            for n, (artist, title, start, _) in enumerate(entries, 1)
        ]
        joga = self.recordings["Jóga"]
        arcas = [a for key, a in self.artists.items() if key.startswith("Arca|")]

        def subject(track: IncomingTrack) -> Subject:
            label = f"{track.credit[0].credited_name} — {track.title}"
            return Subject(type=SubjectType.track, key=track.key, label=label)

        issues = [
            Issue(
                id=f"uncertain_recording:{tracks[0].key}",
                kind=IssueKind.uncertain_recording,
                message="Is 'Turiya and Ramakrishna' one of these recordings, or new?",
                subject=subject(tracks[0]),
                candidates=[
                    IssueCandidate(
                        type=CandidateType.recording,
                        id=joga.id,
                        title=joga.title,
                        subtitle="Björk",
                        detail="0:04",
                        score=82,
                    )
                ],
                choices=[Choice.pick_candidate, Choice.create_new, Choice.skip],
                blocking=False,
            ),
            Issue(
                id=f"metadata_not_found:{tracks[1].key}",
                kind=IssueKind.metadata_not_found,
                message="The station gave no title for this entry.",
                subject=subject(tracks[1]),
                choices=[Choice.keep_as_entered, Choice.skip],
                blocking=False,
            ),
            Issue(
                id="uncertain_artist:Arca",
                kind=IssueKind.uncertain_artist,
                message="Is 'Arca' one of these artists in the library, or new?",
                subject=Subject(type=SubjectType.artist, key="Arca", label="Arca"),
                candidates=[
                    IssueCandidate(
                        type=CandidateType.artist,
                        id=artist.id,
                        title=artist.name,
                        subtitle=artist.disambiguation,
                        score=score,
                    )
                    for artist, score in zip(arcas, (91, 64), strict=True)
                ],
                choices=[Choice.pick_candidate, Choice.create_new, Choice.skip],
                blocking=False,
            ),
            Issue(
                id=f"uncertain_recording:{tracks[3].key}",
                kind=IssueKind.uncertain_recording,
                message="Is 'Dreamworld' one of these recordings in the library, or new?",
                subject=subject(tracks[3]),
                candidates=[
                    IssueCandidate(
                        type=CandidateType.recording,
                        id=self.recordings["Cocoon"].id,
                        title="Cocoon",
                        subtitle="Björk",
                        score=67,
                    )
                ],
                choices=[Choice.pick_candidate, Choice.create_new, Choice.skip],
                answer=Answer(choice=Choice.create_new),
                blocking=False,
            ),
        ]
        proposal = Proposal(
            media=[
                IncomingMedium(
                    key=url,
                    path=None,
                    managed=True,
                    download=True,
                    origin=Origin(kind=SourceKind.nts, uri=url),
                )
            ],
            artists=[
                IncomingArtist(key=artist, name=artist)
                for artist in dict.fromkeys(e[0] for e in entries)
            ],
            loose_tracks=tracks,
            radio=IncomingRadio(
                show=IncomingRadioShow(
                    station=RadioStation.nts, slug="late-night-tapes", name="Late Night Tapes"
                ),
                episode=IncomingRadioEpisode(
                    slug="turiya-and-friends",
                    name="Turiya and friends",
                    broadcast_at=datetime(2025, 3, 21, 22, tzinfo=UTC),
                    url=url,
                    image=IncomingImage(url="https://media.ntslive.co.uk/demo.jpg"),
                ),
                medium_key=url,
                entries=[
                    IncomingRadioEntry(
                        position=n,
                        track_key=track.key,
                        artist_text=track.credit[0].credited_name,
                        title_text=track.title,
                        start_ms=track.start_ms,
                        offset_is_estimate=estimated,
                    )
                    for n, (track, (*_, estimated)) in enumerate(
                        zip(tracks, entries, strict=True), 1
                    )
                ],
            ),
            issues=issues,
        )
        job(
            "turiya",
            ImportKind.nts,
            url,
            -90,
            state=JobState.needs_review,
            proposal=proposal.model_dump(mode="json"),
            log=log(-90, "started", "needs review: 3 open issue(s)"),
        )

    def fix_timestamps(self) -> None:
        """Fixed times for everything (import jobs have their own), and deterministic
        IDs for the derived rows (note links) the code under test creates."""
        self.session.flush()
        for table in (
            *TABLES,
            "artist_credit",
            "artist_credit_name",
            "track",
            "radio_episode",
            "radio_episode_track",
            "note_link",
            "entity_tag",
        ):
            if table == "import_job":
                continue
            self.session.connection().execute(
                text(f"UPDATE {table} SET created_at = :at, updated_at = :at"), {"at": BASE_TIME}
            )
        self.session.connection().execute(
            text("UPDATE note_link SET id = md5(note_id::text || ':' || start::text)::uuid")
        )


def seed(database_url: str, library_dir: Path, watch_dir: Path) -> None:
    music = (Path.home() / "Music").resolve()
    for directory in (library_dir, watch_dir):
        resolved = directory.resolve()
        if resolved == music or resolved.is_relative_to(music):
            raise SeedError(f"refusing to write under ~/Music: {directory}")
        if resolved.exists() and any(resolved.iterdir()):
            raise SeedError(f"not empty: {directory}")

    engine = create_engine(database_url)
    try:
        # Checked before migrating: a database with data is left exactly as it is.
        with engine.connect() as connection:
            for table in TABLES:
                if connection.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() and (
                    connection.execute(text(f"SELECT EXISTS (SELECT FROM {table})")).scalar()
                ):
                    raise SeedError(f"the database is not empty ({table} has rows)")
        config = Config(toml_file=SERVER_DIR / "pyproject.toml")
        config.attributes["database_url"] = database_url
        command.upgrade(config, "head")
        with Session(engine) as session:
            library_dir.mkdir(parents=True, exist_ok=True)
            watch_dir.mkdir(parents=True, exist_ok=True)
            seeder = Seeder(session, library_dir.resolve(), watch_dir.resolve())
            seeder.catalogue()
            seeder.radio()
            seeder.notes_and_tags()
            seeder.jobs(seeder.watch_dirs())
            seeder.fix_timestamps()
            session.commit()
            counts = {
                model.__tablename__: session.exec(select(func.count()).select_from(model)).one()
                for model in (Artist, ReleaseGroup, Recording, Source, ImportJob)
            }
    finally:
        engine.dispose()
    print("seeded:", ", ".join(f"{n} {table}" for table, n in counts.items()))
    print(f"EP_LIBRARY_DIR={library_dir.resolve()}")
    print(f'EP_MEDIA_ROOTS=["{library_dir.resolve()}", "{watch_dir.resolve()}"]')


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill an empty database and library directory with a demo library."
    )
    parser.add_argument("--database-url", required=True, help="SQLAlchemy URL of an empty database")
    parser.add_argument("--library-dir", required=True, type=Path, help="empty or new directory")
    parser.add_argument(
        "--watch-dir", type=Path, help="for the watched folders (default: <library>/../watch)"
    )
    args = parser.parse_args()
    watch_dir = args.watch_dir or args.library_dir.parent / "watch"
    try:
        seed(args.database_url, args.library_dir, watch_dir)
    except SeedError as e:
        print(f"ep.seed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
