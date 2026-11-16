"""The import pipeline shared by every importer: fetch → resolve → match → proposal → apply.

An importer (``Importer``) fetches its input and resolves metadata into a ``Proposal``
(``build``), and, for kinds that download, downloads one medium (``download``). The
framework does the rest, identically for every kind:

- ``match_proposal``: maps incoming release groups, tracks and artists to the library
  with ``ep.matching`` (auto match, new, or an issue for the user). Releases resolved to
  MusicBrainz are matched too: by MBID, else against library rows without an MBID
  (which gain it on apply), and so are their artists;
- ``run_job``: the job state machine. A job with open (blocking) issues stops at
  ``needs_review``; answers (``record_answers``) re-run it from the start, each stage
  consulting the answers by issue id, until nothing is open. Then it downloads and
  applies. Issues about ``optional`` tracks (radio tracklist entries), failed downloads
  and unreadable files do not block: the job applies without what they concern and
  stays in ``needs_review`` until they are answered (re-running it then adds it;
  everything else is reused);
- downloads (``_download``): cover URLs are fetched (``Importer.fetch_cover``) and
  each pending medium is downloaded into its own work
  directory of the job (``job_work_dir``), which survives failures and re-runs: a
  finished download is reused, a partial one is handed to the importer again (to resume
  or overwrite). A medium whose origin is already a source is not downloaded again. The
  work directory is removed when the job is done or dismissed;
- ``apply_proposal``: writes the catalogue rows, sources and cover in the job's single
  transaction, moving downloads to their place in the managed library
  (``Importer.library_path``), then the importer's own rows (``Importer.apply``).
  Idempotent: rows are reused by MBID, sources by location. Files written while
  applying are removed again if the transaction fails.

State machine::

    queued → running → needs_review ─(review / retry)→ queued
                     → applying → done ─(retry: creates nothing new)→ queued
                                → needs_review (optional tracks still open)
                     → failed ─(retry / review)→ queued
    queued, needs_review, failed ─(dismiss)→ dismissed

Jobs leave the inbox only as ``done`` or ``dismissed``. Any exception while running
makes the job ``failed`` with the error and a traceback in its log.
"""

import hashlib
import os
import re
import shutil
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from sqlalchemy import event, update
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, select

from ep import covers
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.importers.musicbrainz import (
    add_isrcs,
    artist_type,
    import_recording,
    import_release,
    propose_recordings,
)
from ep.importers.proposal import (
    NEW,
    SKIP,
    UNDECIDED,
    Answer,
    CandidateType,
    Choice,
    EmbeddedCover,
    ExistingTarget,
    FileCover,
    IncomingArtist,
    IncomingCreditPart,
    IncomingImage,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    LogEntry,
    LogLevel,
    MusicBrainzTarget,
    NewTarget,
    Proposal,
    SkipTarget,
    Subject,
    SubjectType,
    Target,
    TargetKind,
    UndecidedTarget,
    UrlCover,
    issue_id,
)
from ep.matching import Decision, MatchResult, match_artist, match_recording, match_release_group
from ep.matching.normalize import normalize
from ep.media_roots import is_within_media_roots
from ep.metadata import web
from ep.metadata.musicbrainz import (
    MBArtist,
    MBArtistCreditPart,
    MBRelease,
    MusicBrainz,
    render_artist_credit,
)
from ep.metadata.web import WebError
from ep.models import (
    Artist,
    ArtistCredit,
    Base,
    ImportJob,
    ImportKind,
    JobStage,
    JobState,
    Recording,
    ReleaseGroup,
    Source,
    SourceKind,
    Track,
    rel,
)
from ep.models.base import utcnow
from ep.models.catalogue import EntityType, ExternalId
from ep.notes.tags import add_imported_tags
from ep.probe import ProbeError, probe
from ep.settings import Settings


class ImportFailed(Exception):
    """The job cannot go on (input missing, metadata service down): the job fails with
    this message and can be retried."""


class DownloadError(Exception):
    """A pending download failed: becomes a ``download_failed`` issue."""


class JobCancelled(Exception):
    """The user cancelled the running job (``POST /imports/{id}/cancel``)."""


# Transitions the user can make (the worker makes the others).
REVIEWABLE = frozenset({JobState.needs_review, JobState.failed})
RETRYABLE = frozenset({JobState.needs_review, JobState.failed, JobState.done})
DISMISSABLE = frozenset({JobState.needs_review, JobState.failed})
CANCELLABLE = frozenset({JobState.queued, JobState.running})


def job_work_dir(library_dir: Path, job_id: uuid.UUID) -> Path:
    """Where a job keeps its downloads until it is done or dismissed (hidden, so that a
    folder import of the library never picks up a partial download)."""
    return _downloads_dir(library_dir) / str(job_id)


def _downloads_dir(library_dir: Path) -> Path:
    return library_dir / ".downloads"


def discard_work(library_dir: Path, job_id: uuid.UUID) -> None:
    """Remove a job's work directory (downloads not applied); for done or dismissed jobs."""
    shutil.rmtree(job_work_dir(library_dir, job_id), ignore_errors=True)


def discard_stale_work(session: Session, library_dir: Path) -> list[uuid.UUID]:
    """Remove the work directories that ``discard_work`` should have removed (a worker
    stopped first): those of done or dismissed jobs and of jobs that no longer exist.
    For worker start-up. Returns the jobs whose directory was removed."""
    root = _downloads_dir(library_dir)
    found: dict[uuid.UUID, Path] = {}
    for path in root.iterdir() if root.is_dir() else ():
        try:
            found[uuid.UUID(path.name)] = path
        except ValueError:
            continue  # not a job's
    live = session.exec(
        select(ImportJob.id).where(
            col(ImportJob.id).in_(found),
            col(ImportJob.state).not_in([JobState.done, JobState.dismissed]),
        )
    ).all()
    stale = sorted(set(found) - set(live), key=str)
    for job_id in stale:
        discard_work(library_dir, job_id)
    return stale


class JobContext:
    """What a running job's stages share: the session, the user's answers, the issues
    raised so far, the log and progress, and MusicBrainz results of this run.

    Log and progress are committed at once while the proposal is being built (the
    client polls them); while applying they go into the apply transaction.
    """

    def __init__(
        self, session: Session, job: ImportJob, musicbrainz: MusicBrainz, settings: Settings
    ) -> None:
        self.session = session
        self.job = job
        self.musicbrainz = musicbrainz
        self.settings = settings
        self.answers: dict[str, Answer] = {}
        self.issues: list[Issue] = []
        # MusicBrainz releases fetched in this run, by release MBID.
        self.mb_releases: dict[uuid.UUID, MBRelease] = {}
        self.in_transaction = False
        # Undo actions for files written in the apply transaction (run if it fails).
        self.rollback_actions: list[Callable[[], None]] = []

    def load_answers(self) -> None:
        self.answers = {
            key: Answer.model_validate(value) for key, value in self.job.answers.items()
        }

    @property
    def work_dir(self) -> Path:
        return job_work_dir(self.settings.library_dir, self.job.id)

    def save(self) -> None:
        """Commit the job's log and progress (not while applying); raise
        ``JobCancelled`` if the job was cancelled meanwhile (the commit reloads it).
        The worker changes the state only with ``set_state``, never through the row, so
        a commit never overwrites a cancel."""
        if not self.in_transaction:
            self.session.commit()
            if self.job.state is JobState.dismissed:
                raise JobCancelled

    def set_state(self, state: JobState) -> None:
        """Move the job to ``state`` and commit, unless it was cancelled meanwhile
        (``JobCancelled``). A conditional update: it waits for a cancel holding the row
        (``POST /imports/{id}/cancel`` locks it) and then sees it."""
        result = self.session.exec(
            update(ImportJob)
            .where(col(ImportJob.id) == self.job.id, col(ImportJob.state) != JobState.dismissed)
            .values(state=state, updated_at=utcnow())
        )
        if result.rowcount == 0:
            self.session.rollback()
            raise JobCancelled
        self.save()

    def log(self, message: str, level: LogLevel = LogLevel.info) -> None:
        entry = LogEntry(at=utcnow(), level=level, message=message)
        self.job.log = [*self.job.log, entry.model_dump(mode="json")]
        self.save()

    def stage(self, stage: JobStage, total: int | None = None) -> None:
        self.job.stage, self.job.progress_done, self.job.progress_total = stage, 0, total
        self.job.progress_item = None
        self.save()

    def advance(self, done: int = 1) -> None:
        self.job.progress_done += done
        self.job.progress_item = None
        self.save()

    def item_progress(self, fraction: float) -> None:
        """How far the item being worked on (a download) is, 0-1."""
        self.job.progress_item = min(max(fraction, 0.0), 1.0)
        self.save()

    def raise_issue(self, issue: Issue) -> Answer | None:
        """Record an issue; return the user's answer to it if there is a valid one. An
        answer that no longer fits (e.g. the candidate it picked is not offered any more)
        is ignored and the issue is open again."""
        answer = self.answers.get(issue.id)
        if answer is not None and not answer_fits(issue, answer, self.session):
            self.log(f"answer to {issue.id} no longer fits; asking again", LogLevel.warning)
            answer = None
        self.issues = [*(i for i in self.issues if i.id != issue.id), issue]
        issue.answer = answer
        return answer

    def waiting(self, key: str) -> bool:
        """Whether an open issue about the incoming item ``key`` is raised: matching
        leaves the item (and what is in it) alone until it is answered."""
        return any(i.subject.key == key and i.answer is None for i in self.issues)

    def mb_release(self, mbid: uuid.UUID) -> MBRelease:
        if mbid not in self.mb_releases:
            self.mb_releases[mbid] = self.musicbrainz.get_release(mbid)
        return self.mb_releases[mbid]


# Issues whose ``pick_candidate`` answer may name any library row of the type, not
# only a candidate (the user searched the library for another).
PICK_ANY: dict[IssueKind, type[Base]] = {
    IssueKind.uncertain_artist: Artist,
    IssueKind.uncertain_release_group: ReleaseGroup,
    IssueKind.uncertain_recording: Recording,
}


def answer_fits(issue: Issue, answer: Answer, session: Session | None = None) -> bool:
    """Whether the answer is one of the issue's choices; a picked candidate must be one
    of its candidates or (``PICK_ANY``, given a session) a library row of that type."""
    if answer.choice not in issue.choices or answer.choice is Choice.retry:
        return False
    if answer.choice is Choice.pick_candidate:
        if any(candidate.id == answer.candidate_id for candidate in issue.candidates):
            return True
        model = PICK_ANY.get(issue.kind)
        return (
            model is not None
            and session is not None
            and answer.candidate_id is not None
            and session.get(model, answer.candidate_id) is not None
        )
    return True


@dataclass
class Applied:
    """What applying wrote, by incoming key (for ``Importer.apply``)."""

    release_groups: dict[str, ReleaseGroup] = field(default_factory=dict)
    # Track key -> its recording, and the source that plays it (a file or a slice).
    recordings: dict[str, Recording] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    # Medium key -> its file source.
    files: dict[str, Source] = field(default_factory=dict)


class Importer:
    """One import kind. Subclasses implement ``build``; kinds that download also
    ``download``; kinds with rows outside the catalogue (radio) also ``apply``."""

    kind: ImportKind

    def build(self, ctx: JobContext) -> Proposal:
        """Fetch the input and resolve metadata: the incoming items, with targets set
        where resolving decided them and issues raised through ``ctx.raise_issue``.
        Downloads are only described (``IncomingMedium.download``), not done. Raise
        ``ImportFailed`` if the job cannot go on."""
        raise NotImplementedError

    def download(self, ctx: JobContext, medium: IncomingMedium, directory: Path) -> Path:
        """Download a pending medium into ``directory`` and return the file's path in it.

        ``directory`` belongs to this medium and is kept across failures and runs of the
        job until the download succeeds: it may hold partial files of an earlier attempt,
        to resume or overwrite. The framework then reads the file's facts, reuses the
        file in later runs and moves it into the library on apply. Raise
        ``DownloadError`` on failure."""
        raise NotImplementedError(f"{self.kind} imports do not download")

    def fetch_cover(self, url: str) -> bytes:
        """The image at a cover URL (``UrlCover``), fetched before applying; raise
        ``WebError`` on failure."""
        return web.fetch(url)

    def library_path(self, proposal: Proposal, medium: IncomingMedium) -> PurePath:
        """Where a downloaded medium goes in the managed library, relative to it and
        without extension: ``Artist/Release Group/NN Title`` for a track of a release,
        ``Artist/Release Group/Release Group`` for a single-file album, ``Artist/Title``
        for a loose track."""
        return default_library_path(proposal, medium)

    def apply(self, ctx: JobContext, proposal: Proposal, applied: Applied) -> None:
        """Write the importer's own rows (e.g. radio episodes), in the apply transaction
        after the catalogue rows and sources."""


# Running a job


def claim(session: Session, job_id: uuid.UUID) -> ImportJob | None:
    """Move a queued job to running; None if it is not queued (taken, or dismissed)."""
    result = session.exec(
        update(ImportJob)
        .where(col(ImportJob.id) == job_id, col(ImportJob.state) == JobState.queued)
        .values(
            state=JobState.running,
            stage=None,
            progress_done=0,
            progress_total=None,
            progress_item=None,
            error=None,
            updated_at=utcnow(),
        )
    )
    session.commit()
    if result.rowcount == 0:
        return None
    job = session.get(ImportJob, job_id)
    if job is not None:
        session.refresh(job)
    return job


def run_job(
    session: Session,
    job_id: uuid.UUID,
    importers: dict[ImportKind, Importer],
    musicbrainz: MusicBrainz,
    settings: Settings,
) -> JobState | None:
    """Run a queued job to ``needs_review``, ``done`` or ``failed``; returns the new state
    (None if the job was not queued)."""
    job = claim(session, job_id)
    if job is None:
        return None
    ctx = JobContext(session, job, musicbrainz, settings)
    try:
        ctx.load_answers()
        ctx.log("started" if not ctx.answers else f"started with {len(ctx.answers)} answer(s)")
        importer = importers[job.kind]
        proposal = importer.build(ctx)
        match_proposal(ctx, proposal)
        if _stop_for_review(ctx, proposal):
            return job.state
        _download(ctx, importer, proposal)
        if _stop_for_review(ctx, proposal):
            return job.state
        ctx.set_state(JobState.applying)
        ctx.stage(JobStage.apply)
        ctx.in_transaction = True
        summary = apply_proposal(ctx, importer, proposal)
        job.proposal = proposal.model_dump(mode="json")  # with the downloads' final paths
        job.state, job.stage = JobState.done, None
        ctx.log(summary)
        if waiting := proposal.open_issues():
            # Only non-blocking issues are open (optional tracks, failed downloads):
            # applied without them, back in the inbox.
            job.state = JobState.needs_review
            ctx.log(f"applied; needs review: {len(waiting)} open issue(s) left out for now")
        session.commit()
    except JobCancelled:
        session.rollback()
        job = session.get_one(ImportJob, job_id)
        job.stage, job.progress_item = None, None
        job.log = [*job.log, _entry(LogLevel.info, "cancelled")]
        session.commit()
        discard_work(settings.library_dir, job_id)
    except Exception as e:  # the job fails; it stays in the inbox for a retry
        _fail(ctx, job_id, e)
    else:
        # Failed downloads keep their partial files for the retry.
        if not any(i.kind is IssueKind.download_failed for i in proposal.open_issues()):
            discard_work(settings.library_dir, job_id)
    return session.get_one(ImportJob, job_id).state


def _stop_for_review(ctx: JobContext, proposal: Proposal) -> bool:
    proposal.issues = ctx.issues
    ctx.job.proposal = proposal.model_dump(mode="json")
    if blocking := proposal.blocking_issues():
        ctx.set_state(JobState.needs_review)
        ctx.log(f"needs review: {len(blocking)} open issue(s)")
        return True
    ctx.save()
    return False


def _fail(ctx: JobContext, job_id: uuid.UUID, error: Exception) -> None:
    session = ctx.session
    log = list(ctx.job.log) if ctx.in_transaction else None
    session.rollback()
    for action in reversed(ctx.rollback_actions):
        try:
            action()
        except OSError as e:
            log = [*(log or ctx.job.log), _entry(LogLevel.warning, f"undo failed: {e}")]
    job = session.get_one(ImportJob, job_id, with_for_update=True, populate_existing=True)
    if log is not None:  # entries written in the failed apply transaction
        job.log = log
    if job.state is JobState.dismissed:  # cancelled while it went wrong: stays cancelled
        job.log = [*job.log, _entry(LogLevel.info, "cancelled")]
        session.commit()
        discard_work(ctx.settings.library_dir, job_id)
        return
    message = str(error) if isinstance(error, ImportFailed) else f"{type(error).__name__}: {error}"
    job.state, job.error = JobState.failed, message
    job.log = [*job.log, _entry(LogLevel.error, message)]
    if not isinstance(error, ImportFailed):
        trace = "".join(traceback.format_exception(error, limit=-5))
        job.log = [*job.log, _entry(LogLevel.error, trace)]
    session.commit()


def _entry(level: LogLevel, message: str) -> dict:
    return LogEntry(at=utcnow(), level=level, message=message).model_dump(mode="json")


# Downloads


def _download(ctx: JobContext, importer: Importer, proposal: Proposal) -> None:
    used = {t.medium_key for t in proposal.all_tracks() if t.target.kind is not TargetKind.skip}
    pending = [medium for medium in proposal.media if medium.download and medium.key in used]
    for release in proposal.releases:
        if isinstance(release.cover, UrlCover) and release.target.kind is not TargetKind.skip:
            path = fetch_image(ctx, importer, release.cover.url)
            release.cover = FileCover(path=str(path)) if path else None
    if not pending:
        return
    ctx.stage(JobStage.download, len(pending))
    failed: list[Issue] = []
    for medium in pending:
        subject = Subject(type=SubjectType.medium, key=medium.key, label=_medium_label(medium))
        issue = Issue(
            id=issue_id(IssueKind.download_failed, medium.key),
            kind=IssueKind.download_failed,
            message="",
            subject=subject,
            choices=[Choice.retry, Choice.skip],
        )
        if (answer := ctx.answers.get(issue.id)) and answer.choice is Choice.skip:
            issue.message = "download skipped"
            ctx.raise_issue(issue)
            _skip_medium(proposal, medium.key)
        elif (path := _already_downloaded(ctx, medium)) is not None:
            ctx.log(f"{_medium_label(medium)} is in the library already: {path}")
            medium.path, medium.download, medium.managed = path, False, True
        else:
            try:
                _download_medium(ctx, importer, medium)
            except DownloadError as e:
                issue.message = f"download failed: {e}"
                ctx.raise_issue(issue)
                failed.append(issue)
        ctx.advance()
    # With anything else to import, failed downloads do not hold the job up: it applies
    # without their tracks (a retry downloads and adds them).
    if failed and any(not medium.download for medium in proposal.media if medium.key in used):
        for issue in failed:
            issue.blocking = False


def fetch_image(ctx: JobContext, importer: Importer, url: str) -> Path | None:
    """A cover image from the web (``Importer.fetch_cover``), saved in the job's work
    directory (reused by later runs); None (logged) if it cannot be fetched or is not an
    image: the import goes on without it."""
    directory = ctx.work_dir / "covers"
    name = hashlib.sha256(url.encode()).hexdigest()[:16]
    if found := sorted(directory.glob(f"{name}.*")):
        return found[0]
    try:
        data = importer.fetch_cover(url)
    except WebError as e:
        ctx.log(f"cover not fetched: {e}", LogLevel.warning)
        return None
    if (extension := covers.image_extension(data)) is None:
        ctx.log(f"cover {url} is not a JPEG, PNG or WebP image", LogLevel.warning)
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{extension}"
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(data)
    partial.replace(path)
    ctx.log(f"cover fetched: {url}")
    return path


def fetch_incoming_image(ctx: JobContext, importer: Importer, image: IncomingImage) -> None:
    """Fetch an ``IncomingImage`` (if not fetched yet) and set its ``path``."""
    if image.path is None or not Path(image.path).is_file():
        path = fetch_image(ctx, importer, image.url)
        image.path = str(path) if path else None


def _medium_dir(ctx: JobContext, medium: IncomingMedium) -> Path:
    return ctx.work_dir / hashlib.sha256(medium.key.encode()).hexdigest()[:16]


def _download_medium(ctx: JobContext, importer: Importer, medium: IncomingMedium) -> None:
    """Download into the medium's work directory: ``partial/`` while the importer works,
    the file itself once it succeeded (and is readable as audio)."""
    directory = _medium_dir(ctx, medium)
    done = next((p for p in sorted(directory.glob("*")) if p.is_file()), None)
    if done is not None:
        ctx.log(f"using the download of an earlier run: {done.name}")
    else:
        partial = directory / "partial"
        partial.mkdir(parents=True, exist_ok=True)
        file = importer.download(ctx, medium, partial)
        if not file.is_file() or file.parent != partial:
            raise ValueError(f"download returned {file}, not a file in {partial}")
        done = directory / file.name
        file.replace(done)
        shutil.rmtree(partial)
    try:
        result = probe(done)
        sha256 = sha256_file(done)
    except (ProbeError, OSError) as e:
        done.unlink(missing_ok=True)  # download it again on retry
        raise DownloadError(f"the downloaded file cannot be read as audio: {e}") from e
    medium.path, medium.download, medium.managed = str(done), False, True
    for name, value in result.audio_facts().items():
        setattr(medium, name, value)
    medium.sha256 = sha256


def _already_downloaded(ctx: JobContext, medium: IncomingMedium) -> str | None:
    """The file of an earlier import from the same origin, if it is still there."""
    if medium.origin is None:
        return None
    origins = select(Source.id).where(
        Source.kind == medium.origin.kind, Source.uri == medium.origin.uri
    )
    files = ctx.session.exec(
        select(Source.uri).where(Source.kind == SourceKind.file, col(Source.parent_id).in_(origins))
    ).all()
    return next((uri for uri in files if uri and Path(uri).is_file()), None)


def _medium_label(medium: IncomingMedium) -> str:
    return medium.path or (medium.origin.uri if medium.origin else medium.key)


def _skip_medium(proposal: Proposal, key: str) -> None:
    for track in proposal.all_tracks():
        if track.medium_key == key:
            track.target = SKIP


_UNSAFE = re.compile(r"[\x00-\x1f/\\]")
MAX_NAME_BYTES = 180


def safe_name(text: str) -> str:
    """A file or directory name from metadata: no separators or control characters, no
    leading dot, at most ``MAX_NAME_BYTES`` bytes of UTF-8."""
    name = _UNSAFE.sub("_", text).strip().lstrip(".").strip()
    while len(name.encode()) > MAX_NAME_BYTES:
        name = name[:-1]
    return name.rstrip() or "_"


def default_library_path(proposal: Proposal, medium: IncomingMedium) -> PurePath:
    for release in proposal.releases:
        tracks = [t for t in release.tracks if t.medium_key == medium.key]
        if not tracks:
            continue
        folder = PurePath(safe_name(credit_name(release.credit)), safe_name(release.title))
        if len(tracks) > 1 or tracks[0].start_ms or tracks[0].end_ms:
            return folder / safe_name(release.title)
        [track] = tracks
        number = f"{track.position:02d}"
        if len({t.disc for t in release.tracks}) > 1:
            number = f"{track.disc}-{number}"
        return folder / safe_name(f"{number} {track.title}")
    for track in proposal.loose_tracks:
        if track.medium_key == medium.key:
            return PurePath(safe_name(credit_name(track.credit)), safe_name(track.title))
    return PurePath("_unsorted", safe_name(medium.key))


# Review


def record_answers(job: ImportJob, answers: dict[str, Answer]) -> None:
    """Store answers to the job's issues (validated with ``answer_fits`` by the caller;
    ``retry`` answers clear a stored answer) and queue the job again."""
    stored = dict(job.answers)
    for key, answer in answers.items():
        if answer.choice is Choice.retry:
            stored.pop(key, None)
        else:
            stored[key] = answer.model_dump(mode="json")
    job.answers = stored
    requeue(job)


def requeue(job: ImportJob) -> None:
    job.state, job.error = JobState.queued, None


# Matching


def _label(entity: Base) -> str:
    match entity:
        case Artist():
            return entity.name + (f" ({entity.disambiguation})" if entity.disambiguation else "")
        case ReleaseGroup() | Recording():
            return f"{entity.title} — {entity.artist_credit.name}"
    raise TypeError(entity)


def existing(entity: Base) -> ExistingTarget:
    return ExistingTarget(id=entity.id, label=_label(entity))


def _issue_candidate(entity: Base, score: float) -> IssueCandidate:
    match entity:
        case Artist():
            type, title, subtitle = CandidateType.artist, entity.name, entity.disambiguation
            detail = f"MusicBrainz {entity.mbid}" if entity.mbid else None
        case ReleaseGroup():
            type, title, subtitle = (
                CandidateType.release_group,
                entity.title,
                entity.artist_credit.name,
            )
            detail = entity.date
        case Recording():
            type, title, subtitle = CandidateType.recording, entity.title, entity.artist_credit.name
            detail = _length(entity.length_ms)
        case _:
            raise TypeError(entity)
    return IssueCandidate(
        type=type,
        id=entity.id,
        title=title,
        subtitle=subtitle or None,
        detail=detail or None,
        score=round(score, 1),
    )


def _length(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def decide[T: Base](
    ctx: JobContext,
    result: MatchResult[T],
    *,
    kind: IssueKind,
    subject: Subject,
    new: Target,
    can_skip: bool,
    taken: set[uuid.UUID] | None = None,
    blocking: bool = True,
) -> Target:
    """The target for a match result: the automatic match, ``new``, or what the user
    answered to the issue raised for an uncertain match (UNDECIDED while open).

    ``taken``: library rows already used by other incoming items of this job: they are
    not candidates (if the automatic match is one, the others need review).
    ``blocking``: whether the issue, while open, holds up the job (``Issue.blocking``).
    """
    decision = result.decision
    if taken and any(c.entity.id in taken for c in result.candidates):
        candidates = [c for c in result.candidates if c.entity.id not in taken]
        decision = Decision.needs_review if candidates else Decision.new
        result = MatchResult(decision, candidates)
    target: Target
    if decision is Decision.auto_match:
        target = existing(result.candidates[0].entity)
    elif decision is Decision.new:
        target = new
    else:
        what = {
            IssueKind.uncertain_artist: "artist",
            IssueKind.uncertain_release_group: "release group",
            IssueKind.uncertain_recording: "recording",
        }[kind]
        issue = Issue(
            id=issue_id(kind, subject.key),
            kind=kind,
            message=f"Is {subject.label!r} one of these {what}s in the library, or new?",
            subject=subject,
            candidates=[_issue_candidate(c.entity, c.score) for c in result.candidates],
            choices=[
                Choice.pick_candidate,
                Choice.create_new,
                *([Choice.skip] if can_skip else []),
            ],
            blocking=blocking,
        )
        answer = ctx.raise_issue(issue)
        if answer is None:
            return UNDECIDED
        match answer.choice:
            case Choice.pick_candidate:
                entity = next(
                    (c.entity for c in result.candidates if c.entity.id == answer.candidate_id),
                    None,
                ) or ctx.session.get_one(PICK_ANY[kind], answer.candidate_id)
                target = existing(entity)
            case Choice.create_new:
                target = new
            case _:
                target = SKIP
    if taken is not None and isinstance(target, ExistingTarget):
        taken.add(target.id)
    return target


def credit_name(parts: Sequence[IncomingCreditPart]) -> str:
    return "".join(part.credited_name + part.join_phrase for part in parts)


def _artist_names(proposal: Proposal, parts: Sequence[IncomingCreditPart]) -> list[str]:
    return [name for p in parts for name in (p.credited_name, proposal.artist(p.artist_key).name)]


def mb_artist_key(mbid: uuid.UUID) -> str:
    return f"mbid:{mbid}"


def mb_credit(proposal: Proposal, parts: Iterable[MBArtistCreditPart]) -> list[IncomingCreditPart]:
    """Incoming credit parts for a MusicBrainz artist credit, adding its artists to the
    proposal (key ``mbid:<MBID>``, matched like any incoming artist)."""
    credit = []
    for part in parts:
        _add_mb_artist(proposal, part.artist)
        credit.append(
            IncomingCreditPart(
                artist_key=mb_artist_key(part.artist.id),
                credited_name=part.name,
                join_phrase=part.joinphrase,
            )
        )
    return credit


def _add_mb_artist(proposal: Proposal, artist: MBArtist) -> None:
    key = mb_artist_key(artist.id)
    if not any(incoming.key == key for incoming in proposal.artists):
        proposal.artists.append(
            IncomingArtist(
                key=key,
                name=artist.name,
                sort_name=artist.sort_name,
                disambiguation=artist.disambiguation,
                type=artist_type(artist),
                mbid=artist.id,
            )
        )


def mb_release_artists(release: MBRelease) -> list[MBArtist]:
    """Every artist ``import_release`` credits for a release."""
    parts = [*release.artist_credit]
    if release.release_group is not None:
        parts += release.release_group.artist_credit
    for medium in release.media:
        for track in medium.tracks:
            parts += [*track.artist_credit, *track.recording.artist_credit]
    return list({part.artist.id: part.artist for part in parts}.values())


def match_proposal(ctx: JobContext, proposal: Proposal) -> None:
    """Map every undecided incoming item to the library (see the module docstring),
    except items waiting for the answer to an issue raised while building. Artists come
    last, and only those an imported item credits."""
    tracks = proposal.all_tracks()
    ctx.stage(JobStage.match, len(tracks) + len(proposal.artists))
    taken: set[uuid.UUID] = set()
    for release in proposal.releases:
        if ctx.waiting(release.key):
            pass
        elif release.target.kind is TargetKind.skip:
            for track in release.tracks:
                track.target = SKIP
        elif release.release_mbid is not None:
            _match_mb_release(ctx, proposal, release, taken)
        else:
            if release.target.kind is TargetKind.undecided:
                _match_release_group(ctx, proposal, release)
            _match_tracks(ctx, proposal, release, taken)
        ctx.advance(len(release.tracks))
    for track in proposal.loose_tracks:
        if track.target.kind is TargetKind.undecided and not ctx.waiting(track.key):
            # A radio tracklist may play a recording twice: optional tracks neither
            # take library rows from each other nor from the job's other items.
            track.target = _match_track(ctx, proposal, track, None if track.optional else taken)
        ctx.advance()
    _match_artists(ctx, proposal)


def _used_artist_keys(ctx: JobContext, proposal: Proposal) -> dict[str, bool]:
    """The incoming artists something imported credits, each with whether that is only
    ``optional`` tracks (an issue about the artist then does not block the job).
    Tracks that map to a library recording keep its credit: their artists are not
    needed."""
    used: dict[str, bool] = {}

    def use(keys: Iterable[str], optional: bool = False) -> None:
        for key in keys:
            used[key] = used.get(key, True) and optional

    for release in proposal.releases:
        if release.target.kind is TargetKind.skip:
            continue
        use(part.artist_key for part in release.credit)
        if release.release_mbid is not None and release.release_mbid in ctx.mb_releases:
            mb_release = ctx.mb_releases[release.release_mbid]
            use(mb_artist_key(artist.id) for artist in mb_release_artists(mb_release))
    for track in proposal.all_tracks():
        if track.target.kind is TargetKind.skip or isinstance(track.target, ExistingTarget):
            continue
        if track.optional and track.target.kind is TargetKind.undecided:
            continue  # waits for an answer; its artists are matched once it has one
        use((part.artist_key for part in track.credit), track.optional)
    return used


def _match_artists(ctx: JobContext, proposal: Proposal) -> None:
    used = _used_artist_keys(ctx, proposal)
    ctx.job.progress_total = ctx.job.progress_done + len(proposal.artists)
    for artist in proposal.artists:
        if artist.key not in used:
            artist.target = SKIP
        elif artist.target.kind is TargetKind.undecided:
            label = artist.name + (f" ({artist.disambiguation})" if artist.disambiguation else "")
            artist.target = decide(
                ctx,
                match_artist(ctx.session, artist.name, mbid=artist.mbid),
                kind=IssueKind.uncertain_artist,
                subject=Subject(type=SubjectType.artist, key=artist.key, label=label),
                new=_mb_or_new(artist.mbid, label),
                can_skip=False,
                blocking=not used[artist.key],
            )
        ctx.advance()


def _mb_or_new(mbid: uuid.UUID | None, label: str | None = None) -> Target:
    return MusicBrainzTarget(mbid=mbid, label=label) if mbid else NEW


def release_label(release: IncomingRelease) -> str:
    return f"{release.title} — {credit_name(release.credit)}"


def track_label(track: IncomingTrack) -> str:
    return f"{track.position}. {track.title} — {credit_name(track.credit)}"


def _match_mb_release(
    ctx: JobContext, proposal: Proposal, release: IncomingRelease, taken: set[uuid.UUID]
) -> None:
    """A release resolved to MusicBrainz (``release_mbid``): its artists join the
    proposal; the release group is the library's by MBID (an issue if that one is from
    another release), else a library release group without MBID that matches (it gains
    the MBIDs), else new; tracks at positions the library release group has take their
    recordings, the others are matched with ``propose_recordings``."""
    session = ctx.session
    assert release.release_mbid is not None
    mb_release = ctx.mb_release(release.release_mbid)
    for artist in mb_release_artists(mb_release):
        _add_mb_artist(proposal, artist)
    subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
    by_mbid = (
        session.exec(select(ReleaseGroup).where(ReleaseGroup.mbid == release.mbid)).first()
        if release.mbid
        else None
    )
    if by_mbid is not None and by_mbid.release_mbid not in (None, release.release_mbid):
        _release_conflict(ctx, proposal, release, by_mbid, subject, taken)
        return
    if by_mbid is not None:
        release.target = existing(by_mbid)
    elif release.target.kind is TargetKind.undecided or isinstance(
        release.target, MusicBrainzTarget
    ):
        mb_label = f"{mb_release.title} — {render_artist_credit(mb_release.artist_credit)}"
        result = match_release_group(
            session,
            release.title,
            artist=credit_name(release.credit),
            artist_names=_artist_names(proposal, release.credit),
            mbid=release.mbid,
            release_mbid=release.release_mbid,
            barcode=release.barcode,
        )
        release.target = decide(
            ctx,
            result,
            kind=IssueKind.uncertain_release_group,
            subject=subject,
            new=_mb_or_new(release.mbid, mb_label),
            can_skip=True,
        )
    match release.target:
        case ExistingTarget(id=release_group_id):
            _match_tracks_by_position(ctx, release, release_group_id, taken)
        case SkipTarget():
            for track in release.tracks:
                track.target = SKIP
            return
        case UndecidedTarget():
            return
    results = propose_recordings(session, mb_release)
    for track in release.tracks:
        result = results.get(track.mb_track_id) if track.mb_track_id else None
        if track.target.kind is not TargetKind.undecided or result is None:
            continue
        subject = Subject(type=SubjectType.track, key=track.key, label=track_label(track))
        track.target = decide(
            ctx,
            result,
            kind=IssueKind.uncertain_recording,
            subject=subject,
            new=_mb_or_new(track.mbid),
            can_skip=True,
            taken=taken,
        )


def _release_conflict(
    ctx: JobContext,
    proposal: Proposal,
    release: IncomingRelease,
    release_group: ReleaseGroup,
    subject: Subject,
    taken: set[uuid.UUID],
) -> None:
    issue = Issue(
        id=issue_id(IssueKind.release_conflict, release.key),
        kind=IssueKind.release_conflict,
        message=(
            f"{release_label(release)!r} is already in the library from another "
            f"MusicBrainz release ({release_group.release_mbid}); this is release "
            f"{release.release_mbid}. Add these files to the release group in the "
            "library, or skip them."
        ),
        subject=subject,
        candidates=[_issue_candidate(release_group, 100.0)],
        choices=[Choice.pick_candidate, Choice.skip],
    )
    answer = ctx.raise_issue(issue)
    if answer is None:
        release.target = UNDECIDED
    elif answer.choice is Choice.skip:
        release.target = SKIP
        for track in release.tracks:
            track.target = SKIP
    else:
        release.target = existing(release_group)
        release.release_mbid = None  # the tracks attach to the library's tracks
        _match_tracks(ctx, proposal, release, taken)


def _match_tracks_by_position(
    ctx: JobContext, release: IncomingRelease, release_group_id: uuid.UUID, taken: set[uuid.UUID]
) -> None:
    """Tracks of a MusicBrainz release going into a library release group take the
    recording at their position there, unless it is another MusicBrainz recording (or
    the track's recording is in the library as another row)."""
    session = ctx.session
    release_group = session.exec(
        select(ReleaseGroup)
        .where(ReleaseGroup.id == release_group_id)
        .options(selectinload(rel(ReleaseGroup.tracks)).selectinload(rel(Track.recording)))
    ).one()
    at_position = {(t.disc, t.position): t.recording for t in release_group.tracks}
    mbids = [t.mbid for t in release.tracks if t.mbid]
    by_mbid = set(session.exec(select(Recording.mbid).where(col(Recording.mbid).in_(mbids))).all())
    for track in release.tracks:
        recording = at_position.get((track.disc, track.position))
        if track.target.kind is not TargetKind.undecided or recording is None:
            continue
        if recording.mbid == track.mbid or (recording.mbid is None and track.mbid not in by_mbid):
            track.target = existing(recording)
            taken.add(recording.id)


def _match_release_group(ctx: JobContext, proposal: Proposal, release: IncomingRelease) -> None:
    subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
    result = match_release_group(
        ctx.session,
        release.title,
        artist=credit_name(release.credit),
        artist_names=_artist_names(proposal, release.credit),
        mbid=release.mbid,
        barcode=release.barcode,
    )
    release.target = decide(
        ctx,
        result,
        kind=IssueKind.uncertain_release_group,
        subject=subject,
        new=NEW,
        can_skip=True,
    )
    if release.target.kind is TargetKind.skip:
        for track in release.tracks:
            track.target = SKIP


def _match_tracks(
    ctx: JobContext, proposal: Proposal, release: IncomingRelease, taken: set[uuid.UUID]
) -> None:
    """Tracks of a release going into an existing release group take the recording of
    the track at their position there; others are matched like loose tracks."""
    at_position: dict[tuple[int, int], Track] = {}
    if isinstance(release.target, ExistingTarget):
        release_group = ctx.session.exec(
            select(ReleaseGroup)
            .where(ReleaseGroup.id == release.target.id)
            .options(selectinload(rel(ReleaseGroup.tracks)).selectinload(rel(Track.recording)))
        ).one()
        at_position = {(t.disc, t.position): t for t in release_group.tracks}
    for track in release.tracks:
        if track.target.kind is not TargetKind.undecided:
            continue
        if (existing_track := at_position.get((track.disc, track.position))) is not None:
            track.target = existing(existing_track.recording)
            taken.add(existing_track.recording_id)
        elif release.target.kind is not TargetKind.undecided:
            track.target = _match_track(ctx, proposal, track, taken)


def _match_track(
    ctx: JobContext, proposal: Proposal, track: IncomingTrack, taken: set[uuid.UUID] | None
) -> Target:
    subject = Subject(type=SubjectType.track, key=track.key, label=track_label(track))
    result = match_recording(
        ctx.session,
        track.title,
        artist=credit_name(track.credit),
        artist_names=_artist_names(proposal, track.credit),
        length_ms=track.length_ms,
        min_length_ms=track.min_length_ms,
        mbid=track.mbid,
        external_ids=[
            *(("isrc", isrc) for isrc in track.isrcs),
            *((e.scheme, e.value) for e in track.external_ids),
        ],
    )
    return decide(
        ctx,
        result,
        kind=IssueKind.uncertain_recording,
        subject=subject,
        new=NEW,  # with the track's MBID, if it has one
        can_skip=True,
        taken=taken,
        blocking=not track.optional,
    )


# Applying


class _Applier:
    """Writes a fully decided proposal; see ``apply_proposal``."""

    def __init__(self, ctx: JobContext, importer: Importer, proposal: Proposal) -> None:
        self.ctx = ctx
        self.importer = importer
        self.session = ctx.session
        self.proposal = proposal
        self.artists: dict[str, Artist] = {}
        self.applied = Applied()
        # New recordings of this job by identity (``_identities``): two incoming tracks
        # that are the same recording (a radio tracklist playing it twice) become one.
        self.new_recordings: dict[tuple[str, str], Recording] = {}

    def artist(self, key: str) -> Artist:
        if key in self.artists:
            return self.artists[key]
        incoming = self.proposal.artist(key)
        target = incoming.target
        artist: Artist | None = None
        if isinstance(target, ExistingTarget):
            artist = self.session.get_one(Artist, target.id)
            if artist.mbid is None and incoming.mbid and not self._artist_by_mbid(incoming.mbid):
                artist.mbid = incoming.mbid
        elif incoming.mbid:
            artist = self._artist_by_mbid(incoming.mbid)
        if artist is None:
            artist = Artist(
                mbid=incoming.mbid,
                name=incoming.name,
                sort_name=incoming.sort_name or incoming.name,
                disambiguation=incoming.disambiguation,
                type=incoming.type,
            )
            self.session.add(artist)
        self.artists[key] = artist
        return artist

    def _artist_by_mbid(self, mbid: uuid.UUID) -> Artist | None:
        return self.session.exec(select(Artist).where(Artist.mbid == mbid)).first()

    def mb_artists(self, artists: Iterable[MBArtist]) -> dict[uuid.UUID, Artist | None]:
        """The decided library artists for MusicBrainz artists (``import_release``)."""
        keys = {artist.key for artist in self.proposal.artists}
        return {
            artist.id: self.artist(mb_artist_key(artist.id))
            for artist in artists
            if mb_artist_key(artist.id) in keys
        }

    def credit(self, parts: Sequence[IncomingCreditPart]) -> ArtistCredit:
        return get_or_create_artist_credit(
            self.session,
            [CreditPart(self.artist(p.artist_key), p.credited_name, p.join_phrase) for p in parts],
        )

    def recording(self, track: IncomingTrack) -> Recording | None:
        target = track.target
        recording: Recording | None = None
        if track.optional and self._waiting(track):
            self.ctx.log(f"left out until reviewed: {track_label(track)}")
            return None
        if track.medium_key and self.proposal.medium(track.medium_key).download:
            self.ctx.log(f"left out until downloaded: {track_label(track)}")
            return None
        match target:
            case SkipTarget():
                return None
            case ExistingTarget():
                recording = self.session.get_one(Recording, target.id)
                self._attach_mbid(recording, track)
            case MusicBrainzTarget():
                recording = self.session.exec(
                    select(Recording).where(Recording.mbid == target.mbid)
                ).first()
                if recording is None:
                    mb_recording = self.ctx.musicbrainz.get_recording(target.mbid)
                    artists = [part.artist for part in mb_recording.artist_credit]
                    recording = import_recording(
                        self.session, mb_recording, self.mb_artists(artists)
                    )
            case NewTarget():
                identities = self._identities(track)
                recording = next(
                    (self.new_recordings[i] for i in identities if i in self.new_recordings),
                    None,
                )
                if recording is None:
                    recording = Recording(
                        mbid=track.mbid,
                        title=track.title,
                        artist_credit_id=self.credit(track.credit).id,
                        length_ms=track.length_ms,
                    )
                    self.session.add(recording)
                for identity in identities:
                    self.new_recordings.setdefault(identity, recording)
            case UndecidedTarget():
                raise AssertionError(f"track {track.key} is undecided")
        assert recording is not None
        if track.isrcs:
            add_isrcs(self.session, recording.id, track.isrcs)
        if track.external_ids:
            self._external_ids(recording, track)
        self.applied.recordings[track.key] = recording
        return recording

    def _identities(self, track: IncomingTrack) -> list[tuple[str, str]]:
        """What makes a new recording the same as another one of this job: its MBID;
        for optional tracks (radio entries, which repeat) also its ISRCs and external
        IDs and the normalised artist and title text."""
        identities = [("mbid", str(track.mbid))] if track.mbid else []
        if track.optional:
            identities += [("isrc", isrc) for isrc in track.isrcs]
            identities += [(e.scheme, e.value) for e in track.external_ids]
            text = f"{normalize(credit_name(track.credit))}\n{normalize(track.title)}"
            identities.append(("text", text))
        return identities

    def _attach_mbid(self, recording: Recording, track: IncomingTrack) -> None:
        """A library recording without an MBID gains the MBID of the incoming track
        matched to it (like artists and release groups), unless another recording has
        it or another track of this job, matched to the same recording, has another
        MBID (then which one it is stays open)."""
        if recording.mbid is not None or track.mbid is None:
            return
        others = {
            other.mbid
            for other in self.proposal.all_tracks()
            if isinstance(other.target, ExistingTarget)
            and other.target.id == recording.id
            and other.mbid is not None
        }
        if others != {track.mbid}:
            self.ctx.log(
                f"{track_label(track)}: MusicBrainz recording not attached, other tracks of "
                "this import matched to the same recording have other MBIDs",
                LogLevel.warning,
            )
            return
        taken = select(Recording.id).where(Recording.mbid == track.mbid)
        if self.session.exec(taken).first() is None:
            recording.mbid = track.mbid

    def _waiting(self, track: IncomingTrack) -> bool:
        """An optional track waiting for an answer: about itself, or about an artist of
        the new recording it would become."""
        if track.target.kind is TargetKind.undecided:
            return True
        return isinstance(track.target, NewTarget) and any(
            self.proposal.artist(part.artist_key).target.kind is TargetKind.undecided
            for part in track.credit
        )

    def _external_ids(self, recording: Recording, track: IncomingTrack) -> None:
        self.session.flush()
        known = set(
            self.session.exec(
                select(ExternalId.scheme, ExternalId.value).where(
                    ExternalId.entity_type == EntityType.recording,
                    ExternalId.entity_id == recording.id,
                )
            ).all()
        )
        for external in track.external_ids:
            if (external.scheme, external.value) not in known:
                known.add((external.scheme, external.value))
                self.session.add(
                    ExternalId(
                        entity_type=EntityType.recording,
                        entity_id=recording.id,
                        scheme=external.scheme,
                        value=external.value,
                    )
                )

    def release(self, release: IncomingRelease) -> None:
        target = release.target
        if target.kind is TargetKind.skip:
            return
        if release.release_mbid is not None:
            release_group = self._musicbrainz_release(release)
        else:
            if isinstance(target, ExistingTarget):
                release_group = self.session.get_one(ReleaseGroup, target.id)
            else:
                release_group = ReleaseGroup(
                    mbid=release.mbid,
                    title=release.title,
                    artist_credit_id=self.credit(release.credit).id,
                    type=release.type,
                    date=release.date,
                    barcode=release.barcode,
                )
                self.session.add(release_group)
                # Only for a release group the import creates (``ep.notes.tags``).
                add_imported_tags(
                    self.session, EntityType.release_group, release_group.id, release.tags
                )
            self.session.flush()
            positions = {(t.disc, t.position) for t in release_group.tracks}
            for track in release.tracks:
                recording = self.recording(track)
                if recording is None or (track.disc, track.position) in positions:
                    continue
                title = track.title if track.title != recording.title else None
                self.session.add(
                    Track(
                        release_group_id=release_group.id,
                        recording_id=recording.id,
                        disc=track.disc,
                        position=track.position,
                        title=title,
                    )
                )
                positions.add((track.disc, track.position))
        self.applied.release_groups[release.key] = release_group
        self._cover(release, release_group)

    def _musicbrainz_release(self, release: IncomingRelease) -> ReleaseGroup:
        assert release.release_mbid is not None
        mb_release = self.ctx.mb_release(release.release_mbid)
        prematched = {
            track.mb_track_id: self.session.get_one(Recording, track.target.id)
            for track in release.tracks
            if isinstance(track.target, ExistingTarget) and track.mb_track_id
        }
        given = (
            self.session.get_one(ReleaseGroup, release.target.id)
            if isinstance(release.target, ExistingTarget)
            else None
        )
        release_group = import_release(
            self.session,
            mb_release,
            prematched,
            artists=self.mb_artists(mb_release_artists(mb_release)),
            release_group=given,
            tags=release.tags,
        )
        by_track_id = {
            mb_track.id: (medium.position or index, mb_track.position)
            for index, medium in enumerate(mb_release.media, start=1)
            for mb_track in medium.tracks
        }
        rows = {(t.disc, t.position): t for t in release_group.tracks}
        for track in release.tracks:
            if track.target.kind is TargetKind.skip:
                continue
            position = by_track_id.get(track.mb_track_id) if track.mb_track_id else None
            if position is None:
                raise ValueError(
                    f"track {track.key} of MusicBrainz release {release.release_mbid} "
                    "has no track MBID of that release (mb_track_id)"
                )
            recording = rows[position].recording
            if track.isrcs:
                add_isrcs(self.session, recording.id, track.isrcs)
            self.applied.recordings[track.key] = recording
        return release_group

    def _cover(self, release: IncomingRelease, release_group: ReleaseGroup) -> None:
        if release_group.cover_path or release.cover is None:
            return
        data: bytes | None = None
        try:
            match release.cover:
                case EmbeddedCover(medium_key=key):
                    found = probe(Path(self.proposal.medium(key).path or "")).cover
                    data = found.data if found else None
                case FileCover(path=path):
                    if is_within_media_roots(Path(path), self.ctx.settings.media_roots):
                        data = Path(path).read_bytes()
                    else:
                        self.ctx.log(f"cover {path} is outside the media roots", LogLevel.warning)
        except (OSError, ProbeError) as e:
            self.ctx.log(f"cover not read: {e}", LogLevel.warning)
        if data is None:
            return
        try:
            covers.set_release_group_cover(release_group, data, self.ctx.settings.library_dir)
        except ValueError as e:
            self.ctx.log(f"cover not used: {e}", LogLevel.warning)
            return
        written = Path(release_group.cover_path or "")
        self.ctx.rollback_actions.append(lambda: written.unlink(missing_ok=True))

    def sources(self) -> None:
        """File sources for the media, and slices for tracks with a time range. An
        optional track left out (waiting for review) still gets its slice, without a
        recording; it is identified (gains the recording) once the track is applied."""
        tracks = self.proposal.all_tracks()
        recordings = self.applied.recordings
        for medium in self.proposal.media:
            if not medium.path:
                continue
            on_medium = [
                (track, recordings.get(track.key))
                for track in tracks
                if track.medium_key == medium.key
                and (track.key in recordings or (track.optional and track.start_ms is not None))
            ]
            if not on_medium:
                continue
            file = self._file_source(medium)
            self.applied.files[medium.key] = file
            [(first, recording), *more] = on_medium
            whole = not more and first.start_ms is None and first.end_ms is None
            if whole and recording is not None and file.recording_id in (None, recording.id):
                file.recording_id = recording.id
                self.applied.sources[first.key] = file
                continue
            existing_slices = {(s.start_ms, s.end_ms, s.recording_id): s for s in file.children}
            for track, recording in on_medium:
                recording_id = recording.id if recording is not None else None
                start = track.start_ms or 0
                source = existing_slices.get((start, track.end_ms, recording_id))
                if source is None and recording_id is not None:
                    source = existing_slices.pop((start, track.end_ms, None), None)
                    if source is not None:
                        source.recording_id = recording_id
                if source is None:
                    source = Source(
                        kind=SourceKind.slice,
                        parent_id=file.id,
                        start_ms=start,
                        end_ms=track.end_ms,
                        recording_id=recording_id,
                        duration_ms=(track.end_ms - start) if track.end_ms else None,
                    )
                    self.session.add(source)
                existing_slices[(start, track.end_ms, recording_id)] = source
                self.applied.sources[track.key] = source

    def _file_source(self, medium: IncomingMedium) -> Source:
        assert medium.path is not None
        if Path(medium.path).is_relative_to(self.ctx.work_dir):
            medium.path = str(self._place(medium, Path(medium.path)))
        found = self.session.exec(
            select(Source).where(Source.kind == SourceKind.file, Source.uri == medium.path)
        ).first()
        if found is not None:
            return found
        parent_id = None
        if medium.origin is not None:
            origin = self.session.exec(
                select(Source).where(
                    Source.kind == medium.origin.kind, Source.uri == medium.origin.uri
                )
            ).first()
            if origin is None:
                origin = Source(kind=medium.origin.kind, uri=medium.origin.uri)
                self.session.add(origin)
            parent_id = origin.id
        source = Source(
            kind=SourceKind.file,
            uri=medium.path,
            parent_id=parent_id,
            managed=medium.managed,
            codec=medium.codec,
            duration_ms=medium.duration_ms,
            size=medium.size,
            sha256=medium.sha256,
            sample_rate=medium.sample_rate,
            bit_depth=medium.bit_depth,
            bit_rate=medium.bit_rate,
            channels=medium.channels,
            meta={"import_job_id": str(self.ctx.job.id)},
        )
        self.session.add(source)
        return source

    def _place(self, medium: IncomingMedium, downloaded: Path) -> Path:
        """Put a download at its place in the managed library: a hard link (the work
        directory keeps the download until the job is done, so a failed apply loses
        nothing), else a move that is undone if the transaction fails."""
        library = self.ctx.settings.library_dir
        relative = self.importer.library_path(self.proposal, medium)
        base = library / relative
        if not base.resolve().is_relative_to(library.resolve()) or relative.is_absolute():
            raise ValueError(f"library path {relative} is outside the library")
        target = base.with_name(base.name + downloaded.suffix)
        number = 1
        while target.exists() and not target.samefile(downloaded):
            number += 1
            target = base.with_name(f"{base.name} ({number}){downloaded.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target  # linked by an earlier, interrupted apply
        try:
            os.link(downloaded, target)
            self.ctx.rollback_actions.append(lambda: target.unlink(missing_ok=True))
        except OSError:
            downloaded.replace(target)
            self.ctx.rollback_actions.append(lambda: _move(target, downloaded))
        return target


def _move(source: Path, destination: Path) -> None:
    source.replace(destination)


def apply_proposal(ctx: JobContext, importer: Importer, proposal: Proposal) -> str:
    """Write a proposal without open issues; flushes, the caller commits. Returns a
    summary of the rows created."""
    created: Counter[str] = Counter()

    def count_new(session: Session, *_: object) -> None:
        created.update(type(row).__tablename__ for row in session.new)

    event.listen(ctx.session, "before_flush", count_new)
    try:
        applier = _Applier(ctx, importer, proposal)
        for release in proposal.releases:
            applier.release(release)
        for track in proposal.loose_tracks:
            applier.recording(track)
        ctx.session.flush()
        applier.sources()
        ctx.session.flush()
        importer.apply(ctx, proposal, applier.applied)
        ctx.session.flush()
    finally:
        event.remove(ctx.session, "before_flush", count_new)
    tables = ("artist", "release_group", "recording", "track", "source", "external_id")
    ctx.job.created = {table: created[table] for table in tables if created[table]}
    summary = ", ".join(f"{table} {created[table]}" for table in tables if created[table])
    return f"done, created: {summary}" if summary else "done: nothing new to import"


# File facts


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CANCELLABLE",
    "DISMISSABLE",
    "RETRYABLE",
    "REVIEWABLE",
    "Applied",
    "DownloadError",
    "ImportFailed",
    "Importer",
    "IncomingArtist",
    "JobCancelled",
    "JobContext",
    "answer_fits",
    "apply_proposal",
    "discard_work",
    "fetch_image",
    "fetch_incoming_image",
    "job_work_dir",
    "match_proposal",
    "mb_credit",
    "record_answers",
    "requeue",
    "run_job",
    "sha256_file",
]
