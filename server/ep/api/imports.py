"""Import jobs: the user's decision inbox.

``POST /imports`` queues a job; the worker builds its proposal and either applies it or
stops at ``needs_review`` with typed issues. The client answers them by issue id
(``POST /imports/{id}/review``), which queues the job again; ``retry`` runs it again
from the start (keeping the answers), ``dismiss`` takes it out of the inbox.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, func, select

from ep.api.deps import PAGED, PageDep, SessionDep, SettingsDep, get_or_404
from ep.api.entities import entity_reads
from ep.api.errors import CONFLICT, NOT_FOUND, ErrorRead, unprocessable
from ep.api.schemas import (
    CreatedCountsRead,
    ImportCountsRead,
    ImportCreate,
    ImportJobRead,
    ImportJobSummary,
    ImportsSummaryRead,
    ImportStep,
    IssueBriefRead,
    JobAction,
    ProgressRead,
    ReviewRequest,
)
from ep.entities import load_entities
from ep.host_paths import from_host, to_host
from ep.importers.framework import (
    ATTENTION,
    CANCELLABLE,
    DISMISSABLE,
    PICK_ANY,
    RETRYABLE,
    REVIEWABLE,
    answer_fits,
    credit_name,
    discard_work,
    job_work_dir,
    record_answers,
    requeue,
)
from ep.importers.proposal import (
    Answer,
    Choice,
    IssueKind,
    LogEntry,
    Proposal,
    TargetKind,
    UrlCover,
)
from ep.jobs.queue import create_import, enqueue_import
from ep.links import classify
from ep.media_roots import is_within_media_roots
from ep.metadata import bandcamp, nts
from ep.metadata.ytdlp import youtube_url
from ep.models import EntityType, ImportJob, ImportKind, JobStage, JobState, WatchDir
from ep.probe import media_type_of_path
from ep.settings import Settings

router = APIRouter(prefix="/imports", tags=["imports"])


def _proposal(job: ImportJob) -> Proposal | None:
    return Proposal.model_validate(job.proposal) if job.proposal is not None else None


_STEPS = {
    JobStage.fetch: ImportStep.metadata,
    JobStage.resolve: ImportStep.metadata,
    JobStage.match: ImportStep.matching,
    JobStage.propose: ImportStep.matching,
    JobStage.download: ImportStep.downloading,
    JobStage.apply: ImportStep.applying,
}
# Issues a retry tries again.
_FAILURES = frozenset(
    {
        IssueKind.download_failed,
        IssueKind.unreadable_file,
        IssueKind.fetch_failed,
        IssueKind.unexpected_response,
    }
)
_ACTIVE = (JobState.running, JobState.applying, JobState.queued)


def progress_read(job: ImportJob) -> ProgressRead:
    fraction = None
    if job.progress_total:
        done = job.progress_done + (job.progress_item or 0.0)
        fraction = min(done / job.progress_total, 1.0)
    return ProgressRead(
        stage=job.stage,
        step=_STEPS.get(job.stage) if job.stage else None,
        done=job.progress_done,
        total=job.progress_total,
        fraction=fraction,
    )


def _title(proposal: Proposal | None) -> tuple[str | None, str | None]:
    """What is imported: the release, the loose track or the radio episode, and its
    artist credit (the show for an episode)."""
    if proposal is None:
        return None, None
    if proposal.radio is not None:
        return proposal.radio.episode.name, proposal.radio.show.name
    for release in proposal.releases:
        if release.target.kind is not TargetKind.skip:
            return release.title, credit_name(release.credit)
    tracks = [t for t in proposal.loose_tracks if t.target.kind is not TargetKind.skip]
    if tracks:
        return tracks[0].title, credit_name(tracks[0].credit)
    return None, None


def _cover_url(proposal: Proposal | None) -> str | None:
    if proposal is None:
        return None
    if proposal.radio is not None:
        image = proposal.radio.episode.image or proposal.radio.show.image
        return image.url if image is not None else None
    for release in proposal.releases:
        if isinstance(release.cover, UrlCover):
            return release.cover.url
    return None


def _conflict_id(proposal: Proposal | None) -> uuid.UUID | None:
    for issue in proposal.open_issues() if proposal else []:
        if issue.kind is IssueKind.release_conflict and issue.candidates:
            return issue.candidates[0].id
    return None


def _queue_order(session: Session) -> list[uuid.UUID]:
    """Active jobs in the order the worker takes them: the running one, then queued
    ones by when they were queued (their last state change)."""
    rows = session.exec(
        select(ImportJob.id, ImportJob.state)
        .where(col(ImportJob.state).in_(_ACTIVE))
        .order_by(col(ImportJob.updated_at), col(ImportJob.id))
    ).all()
    running = [id for id, state in rows if state is not JobState.queued]
    return running + [id for id, state in rows if state is JobState.queued]


def job_summaries(
    session: Session, settings: Settings, jobs: Sequence[ImportJob]
) -> list[ImportJobSummary]:
    """Summaries of jobs (a fixed number of queries)."""
    proposals = {job.id: _proposal(job) for job in jobs}
    order = _queue_order(session) if any(j.state is JobState.queued for j in jobs) else []
    conflicts = {job.id: _conflict_id(proposals[job.id]) for job in jobs}
    ids = {id for id in conflicts.values() if id is not None}
    existing = (
        entity_reads(
            session, load_entities(session, {(EntityType.release_group, i) for i in ids}).values()
        )
        if ids
        else {}
    )
    summaries = []
    for job in jobs:
        conflict = conflicts[job.id]
        waiting_for = None
        if job.state is JobState.queued and job.id in order:
            position = order.index(job.id)
            waiting_for = order[position - 1] if position > 0 else None
        summaries.append(
            ImportJobSummary(
                **_summary_fields(job, proposals[job.id], settings),
                existing=existing.get(conflict) if conflict else None,
                waiting_for=waiting_for,
            )
        )
    return summaries


def _summary_fields(job: ImportJob, proposal: Proposal | None, settings: Settings) -> dict:
    open_issues = proposal.open_issues() if proposal else []
    actions = []
    if job.state in REVIEWABLE and open_issues:
        actions.append(JobAction.review)
    if job.state in RETRYABLE:
        actions.append(JobAction.retry)
    if job.state in DISMISSABLE:
        actions.append(JobAction.dismiss)
    if job.state in CANCELLABLE:
        actions.append(JobAction.cancel)
    first = next((i for i in open_issues if i.blocking), open_issues[0] if open_issues else None)
    title, subtitle = _title(proposal)
    tracks = proposal.all_tracks() if proposal else []
    link = classify(job.input) if job.kind is not ImportKind.folder else None
    created = job.created or {}
    return {
        "id": job.id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "kind": job.kind,
        "input": job.input,
        "input_host_path": to_host(job.input, settings) if job.kind is ImportKind.folder else None,
        "link_kind": link.link_kind if link else None,
        "watch_dir_id": job.watch_dir_id,
        "state": job.state,
        "progress": progress_read(job),
        "error": job.error,
        "title": title,
        "subtitle": subtitle,
        "cover_url": _cover_url(proposal),
        "first_issue": (
            IssueBriefRead(id=first.id, kind=first.kind, message=first.message) if first else None
        ),
        "open_issues": len(open_issues),
        "counts": ImportCountsRead(
            tracks=sum(1 for t in tracks if t.target.kind is not TargetKind.skip),
            failed=sum(1 for i in open_issues if i.kind in _FAILURES),
        ),
        "created": CreatedCountsRead(
            artists=created.get("artist", 0),
            release_groups=created.get("release_group", 0),
            recordings=created.get("recording", 0),
            tracks=created.get("track", 0),
            sources=created.get("source", 0),
        ),
        "actions": actions,
    }


def job_read(session: Session, settings: Settings, job: ImportJob) -> ImportJobRead:
    [summary] = job_summaries(session, settings, [job])
    return ImportJobRead(
        **summary.model_dump(),
        log=[LogEntry.model_validate(entry) for entry in job.log],
        proposal=_proposal(job),
    )


def check_folder_input(path_text: str, settings: Settings, loc: tuple[str, ...]) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        raise unprocessable(loc, "an absolute path is required", path_text, "value_error")
    if not is_within_media_roots(path, settings.media_roots):
        raise unprocessable(loc, "must be under one of the media roots", path_text, "value_error")
    if not path.is_dir():
        raise unprocessable(loc, "not a directory", path_text, "value_error")
    return path


def check_url_input(kind: ImportKind, url: str, loc: tuple[str, ...]) -> str:
    """The canonical URL for a Bandcamp, YouTube or NTS import (the job's input)."""
    match kind:
        case ImportKind.bandcamp:
            canonical = bandcamp.canonical_url(url)
            expected = "a Bandcamp album or track page URL (https://<name>.bandcamp.com/album/...)"
        case ImportKind.youtube:
            parsed = youtube_url(url)
            canonical = parsed.url if parsed else None
            expected = "a YouTube video or playlist URL"
        case ImportKind.nts:
            ref = nts.episode_ref(url)
            canonical = ref.url if ref else None
            expected = "an NTS episode URL (https://www.nts.live/shows/<show>/episodes/<episode>)"
        case _:
            message = f"{kind} imports are not available yet"
            raise unprocessable(("kind",), message, kind, "value_error")
    if canonical is None:
        raise unprocessable(loc, f"must be {expected}", url, "value_error")
    return canonical


@router.post("", status_code=status.HTTP_201_CREATED)
def create_import_job(
    body: ImportCreate, session: SessionDep, settings: SettingsDep
) -> ImportJobRead:
    """Queue an import. ``folder``: a directory under the media roots, imported in place.
    ``bandcamp``: an album or track page; ``youtube``: a video (with chapters: a
    single-file album) or a playlist (one release group); ``nts``: an NTS episode page
    (the episode with its tracklist matched against the library); downloaded into the
    managed library. URLs are stored canonical (tracking parameters dropped)."""
    if body.kind is ImportKind.folder:
        job_input = str(check_folder_input(from_host(body.input, settings), settings, ("input",)))
    else:
        job_input = check_url_input(body.kind, body.input, ("input",))
    job = create_import(session, body.kind, job_input)
    session.commit()
    return job_read(session, settings, job)


@router.get("/summary")
def get_imports_summary(session: SessionDep) -> ImportsSummaryRead:
    """How many jobs are in each active state, how far the running one is, how many
    directories are watched, and when any job last changed: cheap to poll (an aggregate);
    reload ``GET /imports`` when ``changed_at`` moves."""
    counts = {
        state: func.count(col(ImportJob.id)).filter(col(ImportJob.state) == state)
        for state in (JobState.queued, JobState.needs_review, JobState.failed)
    }
    running = func.count(col(ImportJob.id)).filter(
        col(ImportJob.state).in_((JobState.running, JobState.applying))
    )
    watching = select(func.count(col(WatchDir.id))).where(col(WatchDir.enabled)).scalar_subquery()
    row = (
        session.connection()
        .execute(
            sa_select(
                counts[JobState.queued],
                running,
                counts[JobState.needs_review],
                counts[JobState.failed],
                func.max(ImportJob.updated_at),
                watching,
            )
        )
        .one()
    )
    queued, n_running, needs_review, failed, changed_at, n_watching = row
    fraction = None
    if n_running:
        job = session.exec(
            select(ImportJob)
            .where(col(ImportJob.state).in_((JobState.running, JobState.applying)))
            .order_by(col(ImportJob.updated_at))
            .limit(1)
        ).first()
        fraction = progress_read(job).fraction if job else None
    return ImportsSummaryRead(
        queued=queued,
        running=n_running,
        needs_review=needs_review,
        failed=failed,
        running_fraction=fraction,
        watching=n_watching,
        changed_at=changed_at,
    )


@router.get("", responses=PAGED)
def list_import_jobs(
    session: SessionDep,
    settings: SettingsDep,
    page: PageDep,
    state: Annotated[list[JobState] | None, Query()] = None,
    kind: ImportKind | None = None,
    attention: Annotated[
        bool | None,
        Query(
            description="true: only jobs that need attention (``needs_review``, ``failed``: "
            "the inbox); false: only the others. Combines with ``state``."
        ),
    ] = None,
) -> list[ImportJobSummary]:
    """Jobs, newest first. The inbox: ``attention=true`` (``needs_review`` and
    ``failed`` jobs, waiting for an answer, a retry or a dismiss); the rest of the queue
    without them: ``attention=false``. Each with what it imports (title, cover, counts),
    its progress, what it is stuck on and the job a queued one waits for."""
    statement = select(ImportJob)
    if state:
        statement = statement.where(col(ImportJob.state).in_(state))
    if attention is not None:
        in_inbox = col(ImportJob.state).in_(ATTENTION)
        statement = statement.where(in_inbox if attention else ~in_inbox)
    if kind:
        statement = statement.where(ImportJob.kind == kind)
    jobs = session.exec(
        page.apply(session, statement).order_by(col(ImportJob.created_at).desc(), col(ImportJob.id))
    ).all()
    return job_summaries(session, settings, jobs)


@router.get("/{id}", responses=NOT_FOUND)
def get_import_job(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ImportJobRead:
    """State, progress, log and proposal (with its issues) of a job."""
    return job_read(session, settings, get_or_404(session, ImportJob, id))


def _job_in(session: Session, id: uuid.UUID, states: frozenset[JobState], action: str) -> ImportJob:
    """The job, locked for this transaction (the worker claims a queued job with a
    conditional update, which waits for the lock and then sees the new state)."""
    job = session.get(ImportJob, id, with_for_update=True)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"import_job {id} not found")
    if job.state not in states:
        raise HTTPException(status.HTTP_409_CONFLICT, f"a {job.state} job cannot be {action}")
    return job


@router.post("/{id}/review", responses=NOT_FOUND | CONFLICT)
def review_import_job(
    id: uuid.UUID, body: ReviewRequest, session: SessionDep, settings: SettingsDep
) -> ImportJobRead:
    """Answer issues by id, then run the job again with the answers.

    Each answer must be one of its issue's ``choices`` (and, for ``pick_candidate``, name
    one of its candidates); answered issues can be answered again. ``retry`` clears a
    stored answer and simply runs again. 422 for an unknown issue or a choice that does
    not fit; 409 unless the job is ``needs_review`` or ``failed``.
    """
    job = _job_in(session, id, REVIEWABLE, "reviewed")
    issues = {issue.id: issue for issue in (_proposal(job) or Proposal()).issues}
    answers: dict[str, Answer] = {}
    for index, given in enumerate(body.answers):
        issue = issues.get(given.issue_id)
        if issue is None:
            raise unprocessable(("answers", index, "issue_id"), "no such issue", given.issue_id)
        answer = Answer(choice=given.choice, candidate_id=given.candidate_id)
        fits = (
            Choice.retry in issue.choices
            if answer.choice is Choice.retry
            else answer_fits(issue, answer, session)
        )
        if not fits:
            message = f"not one of the issue's choices ({', '.join(issue.choices)})"
            if answer.choice is Choice.pick_candidate:
                message = "candidate_id must be the id of one of the issue's candidates"
                if issue.kind in PICK_ANY:
                    message += " or of a library row of their type"
            raise unprocessable(
                ("answers", index), message, given.model_dump(mode="json"), "value_error"
            )
        answers[issue.id] = answer
    record_answers(job, answers)
    enqueue_import(session, job.id)
    session.commit()
    return job_read(session, settings, job)


@router.post("/{id}/retry", responses=NOT_FOUND | CONFLICT)
def retry_import_job(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ImportJobRead:
    """Run a ``needs_review``, ``failed`` or ``done`` job again from the start, with its
    answers: failed downloads and reads are tried again (``ImportCountsRead.failed``).
    A done job creates nothing new: files already imported are left out."""
    job = _job_in(session, id, RETRYABLE, "retried")
    requeue(job)
    enqueue_import(session, job.id)
    session.commit()
    return job_read(session, settings, job)


@router.post("/{id}/dismiss", responses=NOT_FOUND | CONFLICT)
def dismiss_import_job(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ImportJobRead:
    """Take a ``needs_review`` or ``failed`` job out of the inbox. Nothing more is
    imported, and its downloads are deleted; the watcher does not queue the folder again
    until it changes."""
    job = _job_in(session, id, DISMISSABLE, "dismissed")
    job.state = JobState.dismissed
    session.commit()
    discard_work(settings.library_dir, job.id)
    return job_read(session, settings, job)


@router.post("/{id}/cancel", responses=NOT_FOUND | CONFLICT)
def cancel_import_job(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ImportJobRead:
    """Stop a ``queued`` or ``running`` job and take it out of the queue (it becomes
    ``dismissed``). A running job stops at its next step (a running download is
    killed); what it fetched is deleted and nothing is imported. A job already writing
    to the library (``applying``) finishes: 409."""
    job = _job_in(session, id, CANCELLABLE, "cancelled")
    running = job.state is JobState.running
    job.state = JobState.dismissed
    session.commit()
    if not running:  # a running job's worker deletes them once it stops
        discard_work(settings.library_dir, job.id)
    return job_read(session, settings, job)


@router.get(
    "/{id}/stream",
    response_class=FileResponse,
    responses=NOT_FOUND
    | {
        status.HTTP_409_CONFLICT: {
            "model": ErrorRead,
            "description": "The track's audio is not available (not downloaded yet)",
        },
        status.HTTP_200_OK: {"content": {"audio/*": {}}, "description": "The whole audio file"},
        status.HTTP_206_PARTIAL_CONTENT: {
            "content": {"audio/*": {}},
            "description": "The requested byte range (Range header)",
        },
    },
)
def stream_import_track(
    id: uuid.UUID,
    track_key: Annotated[str, Query(min_length=1)],
    session: SessionDep,
    settings: SettingsDep,
) -> FileResponse:
    """The audio file of an incoming track (``IncomingTrack.key``), to preview it in
    review: the whole file of its medium, with HTTP Range; play the track's
    ``start_ms``..``end_ms``. 409 while the medium is not downloaded (or has no audio)."""
    job = get_or_404(session, ImportJob, id)
    proposal = _proposal(job) or Proposal()
    track = next((t for t in proposal.all_tracks() if t.key == track_key), None)
    if track is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"import_job {id} has no track {track_key!r}"
        )
    medium = next((m for m in proposal.media if m.key == track.medium_key), None)
    if medium is None or medium.path is None or medium.download:
        raise HTTPException(status.HTTP_409_CONFLICT, "the track's audio is not available")
    path = Path(medium.path)
    allowed = [*settings.media_roots, job_work_dir(settings.library_dir, job.id)]
    if not is_within_media_roots(path, allowed) or not path.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "the track's audio is not available")
    return FileResponse(path, media_type=medium.media_type or media_type_of_path(path))
