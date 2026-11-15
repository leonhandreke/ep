"""The import pipeline shared by every importer: fetch → resolve → match → proposal → apply.

An importer (``Importer``) fetches its input and resolves metadata into a ``Proposal``
(``build``), and, for kinds that download, performs pending downloads (``download``).
The framework does the rest, identically for every kind:

- ``match_proposal``: maps incoming artists, release groups and tracks to the library
  with ``ep.matching`` (auto match, new, or an issue for the user);
- ``run_job``: the job state machine. A job with open issues stops at ``needs_review``;
  answers (``record_answers``) re-run it from the start, each stage consulting the
  answers by issue id, until nothing is open. Then it applies;
- ``apply_proposal``: writes the catalogue rows, sources and cover in the job's single
  transaction. Idempotent: rows are reused by MBID, sources by location.

State machine::

    queued → running → needs_review ─(review / retry)→ queued
                     → applying → done ─(retry: creates nothing new)→ queued
                     → failed ─(retry / review)→ queued
    queued, needs_review, failed ─(dismiss)→ dismissed

Jobs leave the inbox only as ``done`` or ``dismissed``.
"""

import hashlib
import traceback
import uuid
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import event, update
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, select

from ep import covers
from ep.credits import CreditPart, get_or_create_artist_credit
from ep.importers.musicbrainz import (
    add_isrcs,
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
    CoverKind,
    IncomingArtist,
    IncomingCreditPart,
    IncomingMedium,
    IncomingRelease,
    IncomingTrack,
    Issue,
    IssueCandidate,
    IssueKind,
    LogEntry,
    LogLevel,
    Proposal,
    Subject,
    SubjectType,
    Target,
    TargetKind,
    issue_id,
)
from ep.matching import Decision, MatchResult, match_artist, match_recording, match_release_group
from ep.metadata.musicbrainz import MBRelease, MusicBrainz, render_artist_credit
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
from ep.probe import ProbeError, probe
from ep.settings import Settings


class ImportFailed(Exception):
    """The job cannot go on (input missing, metadata service down): the job fails with
    this message and can be retried."""


class DownloadError(Exception):
    """A pending download failed: becomes a ``download_failed`` issue."""


# Transitions the user can make (the worker makes the others).
REVIEWABLE = frozenset({JobState.needs_review, JobState.failed})
RETRYABLE = frozenset({JobState.needs_review, JobState.failed, JobState.done})
DISMISSABLE = frozenset({JobState.queued, JobState.needs_review, JobState.failed})


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
        self.answers = {key: Answer.model_validate(value) for key, value in job.answers.items()}
        self.issues: list[Issue] = []
        # MusicBrainz releases fetched in this run, by release MBID.
        self.mb_releases: dict[uuid.UUID, MBRelease] = {}
        self.in_transaction = False

    def save(self) -> None:
        """Commit the job's state, log and progress (not while applying)."""
        if not self.in_transaction:
            self.session.commit()

    def log(self, message: str, level: LogLevel = LogLevel.info) -> None:
        entry = LogEntry(at=utcnow(), level=level, message=message)
        self.job.log = [*self.job.log, entry.model_dump(mode="json")]
        self.save()

    def stage(self, stage: JobStage, total: int | None = None) -> None:
        self.job.stage, self.job.progress_done, self.job.progress_total = stage, 0, total
        self.save()

    def advance(self, done: int = 1) -> None:
        self.job.progress_done += done
        self.save()

    def raise_issue(self, issue: Issue) -> Answer | None:
        """Record an issue; return the user's answer to it if there is a valid one."""
        answer = self.answers.get(issue.id)
        if answer is not None and not answer_fits(issue, answer):
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


def answer_fits(issue: Issue, answer: Answer) -> bool:
    if answer.choice not in issue.choices or answer.choice is Choice.retry:
        return False
    if answer.choice is Choice.pick_candidate:
        return any(candidate.id == answer.candidate_id for candidate in issue.candidates)
    return True


class Importer:
    """One import kind. Subclasses implement ``build``; kinds that download also
    ``download``."""

    kind: ImportKind

    def build(self, ctx: JobContext) -> Proposal:
        """Fetch the input and resolve metadata: the incoming items, with targets set
        where resolving decided them (e.g. MusicBrainz) and issues raised through
        ``ctx.raise_issue``. Raise ``ImportFailed`` if the job cannot go on."""
        raise NotImplementedError

    def download(self, ctx: JobContext, medium: IncomingMedium) -> None:
        """Download a pending medium: set its ``path`` and file facts, clear ``download``.
        Raise ``DownloadError`` on failure."""
        raise NotImplementedError(f"{self.kind} imports do not download")


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
    ctx.log("started" if not job.answers else f"started with {len(job.answers)} answer(s)")
    try:
        proposal = importers[job.kind].build(ctx)
        match_proposal(ctx, proposal)
        if _stop_for_review(ctx, proposal):
            return job.state
        _download(ctx, importers[job.kind], proposal)
        if _stop_for_review(ctx, proposal):
            return job.state
        job.state = JobState.applying
        ctx.stage(JobStage.apply)
        ctx.in_transaction = True
        summary = apply_proposal(ctx, proposal)
        job.state, job.stage = JobState.done, None
        ctx.log(summary)
        session.commit()
    except Exception as e:  # the job fails; it stays in the inbox for a retry
        _fail(ctx, job_id, e)
    return session.get_one(ImportJob, job_id).state


def _stop_for_review(ctx: JobContext, proposal: Proposal) -> bool:
    proposal.issues = ctx.issues
    ctx.job.proposal = proposal.model_dump(mode="json")
    if open_issues := proposal.open_issues():
        ctx.job.state = JobState.needs_review
        ctx.log(f"needs review: {len(open_issues)} open issue(s)")
        return True
    ctx.save()
    return False


def _fail(ctx: JobContext, job_id: uuid.UUID, error: Exception) -> None:
    session = ctx.session
    log = list(ctx.job.log) if ctx.in_transaction else None
    session.rollback()
    job = session.get_one(ImportJob, job_id)
    session.refresh(job)
    if log is not None:  # entries written in the failed apply transaction
        job.log = log
    message = str(error) if isinstance(error, ImportFailed) else f"{type(error).__name__}: {error}"
    job.state, job.error = JobState.failed, message
    entry = LogEntry(at=utcnow(), level=LogLevel.error, message=message)
    job.log = [*job.log, entry.model_dump(mode="json")]
    if not isinstance(error, ImportFailed):
        trace = "".join(traceback.format_exception(error, limit=-5))
        entry = LogEntry(at=utcnow(), level=LogLevel.error, message=trace)
        job.log = [*job.log, entry.model_dump(mode="json")]
    session.commit()


def _download(ctx: JobContext, importer: Importer, proposal: Proposal) -> None:
    used = {t.medium_key for t in proposal.all_tracks() if t.target.kind is not TargetKind.skip}
    pending = [medium for medium in proposal.media if medium.download and medium.key in used]
    if not pending:
        return
    ctx.stage(JobStage.download, len(pending))
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
        else:
            try:
                importer.download(ctx, medium)
            except DownloadError as e:
                issue.message = f"download failed: {e}"
                ctx.raise_issue(issue)
        ctx.advance()


def _medium_label(medium: IncomingMedium) -> str:
    return medium.path or (medium.origin.uri if medium.origin else medium.key)


def _skip_medium(proposal: Proposal, key: str) -> None:
    for track in proposal.all_tracks():
        if track.medium_key == key:
            track.target = SKIP


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


def existing(entity: Base) -> Target:
    return Target(kind=TargetKind.existing, id=entity.id, label=_label(entity))


def _issue_candidate(entity: Base, score: float) -> IssueCandidate:
    match entity:
        case Artist():
            type, title, subtitle = CandidateType.artist, entity.name, entity.disambiguation
            detail = None
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
        detail=detail,
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
) -> Target:
    """The target for a match result: the automatic match, ``new``, or what the user
    answered to the issue raised for an uncertain match (UNDECIDED while open).

    ``taken``: library rows already used by other incoming items of this job; an
    automatic match to one of them needs review instead.
    """
    decision = result.decision
    if decision is Decision.auto_match and taken and result.candidates[0].entity.id in taken:
        decision = Decision.needs_review
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
        )
        answer = ctx.raise_issue(issue)
        if answer is None:
            return UNDECIDED
        match answer.choice:
            case Choice.pick_candidate:
                entity = next(
                    c.entity for c in result.candidates if c.entity.id == answer.candidate_id
                )
                target = existing(entity)
            case Choice.create_new:
                target = new
            case _:
                target = SKIP
    if taken is not None and target.kind is TargetKind.existing and target.id:
        taken.add(target.id)
    return target


def credit_name(parts: Sequence[IncomingCreditPart]) -> str:
    return "".join(part.credited_name + part.join_phrase for part in parts)


def _artist_names(proposal: Proposal, parts: Sequence[IncomingCreditPart]) -> list[str]:
    return [name for p in parts for name in (p.credited_name, proposal.artist(p.artist_key).name)]


def match_proposal(ctx: JobContext, proposal: Proposal) -> None:
    """Map every undecided incoming item to the library (see the module docstring),
    except items waiting for the answer to an issue raised while building."""
    tracks = proposal.all_tracks()
    ctx.stage(JobStage.match, len(proposal.artists) + len(tracks))
    session = ctx.session
    for artist in proposal.artists:
        if artist.target.kind is TargetKind.undecided:
            subject = Subject(type=SubjectType.artist, key=artist.key, label=artist.name)
            result = match_artist(session, artist.name, mbid=artist.mbid)
            new = _mb_or_new(artist.mbid)
            artist.target = decide(
                ctx,
                result,
                kind=IssueKind.uncertain_artist,
                subject=subject,
                new=new,
                can_skip=False,
            )
        ctx.advance()
    taken: set[uuid.UUID] = set()
    for release in proposal.releases:
        if ctx.waiting(release.key):
            pass
        elif release.target.kind is TargetKind.skip:
            for track in release.tracks:
                track.target = SKIP
        elif release.target.kind is TargetKind.musicbrainz and release.release_mbid:
            _match_mb_release(ctx, proposal, release, taken)
        else:
            if release.target.kind is TargetKind.undecided:
                _match_release_group(ctx, proposal, release)
            _match_tracks(ctx, proposal, release, taken)
        ctx.advance(len(release.tracks))
    for track in proposal.loose_tracks:
        if track.target.kind is TargetKind.undecided:
            track.target = _match_track(ctx, proposal, track, taken)
        ctx.advance()


def _mb_or_new(mbid: uuid.UUID | None) -> Target:
    return Target(kind=TargetKind.musicbrainz, mbid=mbid) if mbid else NEW


def release_label(release: IncomingRelease) -> str:
    return f"{release.title} — {credit_name(release.credit)}"


def track_label(track: IncomingTrack) -> str:
    return f"{track.position}. {track.title} — {credit_name(track.credit)}"


def _match_mb_release(
    ctx: JobContext, proposal: Proposal, release: IncomingRelease, taken: set[uuid.UUID]
) -> None:
    """A release resolved to MusicBrainz: the release group by MBID (an issue if it is
    in the library from another release), recordings with ``propose_recordings``."""
    session = ctx.session
    assert release.release_mbid is not None
    mb_release = ctx.mb_release(release.release_mbid)
    release_group = session.exec(
        select(ReleaseGroup).where(ReleaseGroup.mbid == release.mbid)
    ).first()
    if release_group is not None and release_group.release_mbid not in (None, release.release_mbid):
        subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
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
        return
    release.target = (
        existing(release_group)
        if release_group
        else Target(
            kind=TargetKind.musicbrainz,
            mbid=release.mbid,
            label=f"{mb_release.title} — {render_artist_credit(mb_release.artist_credit)}",
        )
    )
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
            new=Target(kind=TargetKind.musicbrainz, mbid=track.mbid),
            can_skip=True,
            taken=taken,
        )


def _match_release_group(ctx: JobContext, proposal: Proposal, release: IncomingRelease) -> None:
    subject = Subject(type=SubjectType.release, key=release.key, label=release_label(release))
    result = match_release_group(
        ctx.session,
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
    if release.target.kind is TargetKind.existing:
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
    ctx: JobContext, proposal: Proposal, track: IncomingTrack, taken: set[uuid.UUID]
) -> Target:
    subject = Subject(type=SubjectType.track, key=track.key, label=track_label(track))
    result = match_recording(
        ctx.session,
        track.title,
        artist=credit_name(track.credit),
        artist_names=_artist_names(proposal, track.credit),
        length_ms=track.length_ms,
        mbid=track.mbid,
        external_ids=[("isrc", isrc) for isrc in track.isrcs],
    )
    return decide(
        ctx,
        result,
        kind=IssueKind.uncertain_recording,
        subject=subject,
        new=NEW,
        can_skip=True,
        taken=taken,
    )


# Applying


class _Applier:
    """Writes a fully decided proposal; see ``apply_proposal``."""

    def __init__(self, ctx: JobContext, proposal: Proposal) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.proposal = proposal
        self.artists: dict[str, Artist] = {}
        # Incoming track key -> the recording it became.
        self.recordings: dict[str, Recording] = {}

    def artist(self, key: str) -> Artist:
        if key in self.artists:
            return self.artists[key]
        incoming = self.proposal.artist(key)
        target = incoming.target
        artist: Artist | None = None
        if target.kind is TargetKind.existing:
            artist = self.session.get_one(Artist, target.id)
        elif incoming.mbid:
            artist = self.session.exec(select(Artist).where(Artist.mbid == incoming.mbid)).first()
        if artist is None:
            artist = Artist(
                mbid=incoming.mbid,
                name=incoming.name,
                sort_name=incoming.sort_name or incoming.name,
            )
            self.session.add(artist)
        self.artists[key] = artist
        return artist

    def credit(self, parts: Sequence[IncomingCreditPart]) -> ArtistCredit:
        return get_or_create_artist_credit(
            self.session,
            [CreditPart(self.artist(p.artist_key), p.credited_name, p.join_phrase) for p in parts],
        )

    def recording(self, track: IncomingTrack) -> Recording | None:
        target = track.target
        recording: Recording | None = None
        match target.kind:
            case TargetKind.skip:
                return None
            case TargetKind.existing:
                recording = self.session.get_one(Recording, target.id)
            case TargetKind.musicbrainz:
                recording = self.session.exec(
                    select(Recording).where(Recording.mbid == target.mbid)
                ).first()
                if recording is None and target.mbid:
                    mb_recording = self.ctx.musicbrainz.get_recording(target.mbid)
                    recording = import_recording(self.session, mb_recording)
            case TargetKind.new:
                recording = Recording(
                    mbid=track.mbid,
                    title=track.title,
                    artist_credit_id=self.credit(track.credit).id,
                    length_ms=track.length_ms,
                )
                self.session.add(recording)
            case TargetKind.undecided:
                raise AssertionError(f"track {track.key} is undecided")
        assert recording is not None
        if track.isrcs:
            add_isrcs(self.session, recording.id, track.isrcs)
        self.recordings[track.key] = recording
        return recording

    def release(self, release: IncomingRelease) -> None:
        target = release.target
        if target.kind is TargetKind.skip:
            return
        if target.kind is TargetKind.musicbrainz and release.release_mbid:
            release_group = self._musicbrainz_release(release)
        else:
            if target.kind is TargetKind.existing:
                release_group = self.session.get_one(ReleaseGroup, target.id)
            else:
                release_group = ReleaseGroup(
                    mbid=release.mbid,
                    release_mbid=release.release_mbid,
                    title=release.title,
                    artist_credit_id=self.credit(release.credit).id,
                    type=release.type,
                    date=release.date,
                    barcode=release.barcode,
                )
                self.session.add(release_group)
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
        self._cover(release, release_group)

    def _musicbrainz_release(self, release: IncomingRelease) -> ReleaseGroup:
        assert release.release_mbid is not None
        mb_release = self.ctx.mb_release(release.release_mbid)
        prematched = {
            track.mb_track_id: self.session.get_one(Recording, track.target.id)
            for track in release.tracks
            if track.target.kind is TargetKind.existing and track.mb_track_id
        }
        release_group = import_release(self.session, mb_release, prematched)
        by_track_id = {
            mb_track.id: (medium.position or index, mb_track.position)
            for index, medium in enumerate(mb_release.media, start=1)
            for mb_track in medium.tracks
        }
        rows = {(t.disc, t.position): t for t in release_group.tracks}
        for track in release.tracks:
            position = by_track_id.get(track.mb_track_id) if track.mb_track_id else None
            if track.target.kind is TargetKind.skip or position is None:
                continue
            recording = rows[position].recording
            if track.isrcs:
                add_isrcs(self.session, recording.id, track.isrcs)
            self.recordings[track.key] = recording
        return release_group

    def _cover(self, release: IncomingRelease, release_group: ReleaseGroup) -> None:
        if release_group.cover_path or release.cover is None:
            return
        cover = release.cover
        data: bytes | None = None
        try:
            if cover.kind is CoverKind.embedded and cover.medium_key:
                medium = self.proposal.medium(cover.medium_key)
                found = probe(Path(medium.path or "")).cover
                data = found.data if found else None
            elif cover.kind is CoverKind.file and cover.path:
                data = Path(cover.path).read_bytes()
        except (OSError, ProbeError) as e:
            self.ctx.log(f"cover not read: {e}", LogLevel.warning)
        if data is None:
            return
        try:
            covers.set_release_group_cover(release_group, data, self.ctx.settings.library_dir)
        except ValueError as e:
            self.ctx.log(f"cover not used: {e}", LogLevel.warning)

    def sources(self) -> None:
        tracks = self.proposal.all_tracks()
        for medium in self.proposal.media:
            if medium.existing_source_id or not medium.path:
                continue
            playing = [
                (track, self.recordings[track.key])
                for track in tracks
                if track.medium_key == medium.key and track.key in self.recordings
            ]
            if not playing:
                continue
            file = self._file_source(medium)
            whole = [p for p in playing if p[0].start_ms is None and p[0].end_ms is None]
            if len(playing) == 1 and whole:
                file.recording_id = playing[0][1].id
                continue
            existing_slices = {(s.start_ms, s.end_ms, s.recording_id) for s in file.children}
            for track, recording in playing:
                key = (track.start_ms or 0, track.end_ms, recording.id)
                if key in existing_slices:
                    continue
                self.session.add(
                    Source(
                        kind=SourceKind.slice,
                        parent_id=file.id,
                        start_ms=track.start_ms or 0,
                        end_ms=track.end_ms,
                        recording_id=recording.id,
                        duration_ms=(track.end_ms - (track.start_ms or 0))
                        if track.end_ms
                        else None,
                    )
                )
                existing_slices.add(key)

    def _file_source(self, medium: IncomingMedium) -> Source:
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
            meta={"import_job_id": str(self.ctx.job.id)},
        )
        self.session.add(source)
        return source


def apply_proposal(ctx: JobContext, proposal: Proposal) -> str:
    """Write a proposal without open issues; flushes, the caller commits. Returns a
    summary of the rows created."""
    created: Counter[str] = Counter()

    def count_new(session: Session, *_: object) -> None:
        created.update(type(row).__tablename__ for row in session.new)

    event.listen(ctx.session, "before_flush", count_new)
    try:
        applier = _Applier(ctx, proposal)
        for release in proposal.releases:
            applier.release(release)
        for track in proposal.loose_tracks:
            applier.recording(track)
        ctx.session.flush()
        applier.sources()
        ctx.session.flush()
    finally:
        event.remove(ctx.session, "before_flush", count_new)
    tables = ("artist", "release_group", "recording", "track", "source", "external_id")
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
    "DISMISSABLE",
    "RETRYABLE",
    "REVIEWABLE",
    "DownloadError",
    "ImportFailed",
    "Importer",
    "IncomingArtist",
    "JobContext",
    "answer_fits",
    "apply_proposal",
    "match_proposal",
    "record_answers",
    "requeue",
    "run_job",
    "sha256_file",
]
