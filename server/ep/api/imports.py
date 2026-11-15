"""Import jobs: the user's decision inbox.

``POST /imports`` queues a job; the worker builds its proposal and either applies it or
stops at ``needs_review`` with typed issues. The client answers them by issue id
(``POST /imports/{id}/review``), which queues the job again; ``retry`` runs it again
from the start (keeping the answers), ``dismiss`` takes it out of the inbox.
"""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import Session, col, select

from ep.api.deps import PageDep, SessionDep, SettingsDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND, unprocessable
from ep.api.schemas import (
    ImportCreate,
    ImportJobRead,
    ImportJobSummary,
    JobAction,
    ProgressRead,
    ReviewRequest,
)
from ep.importers.framework import (
    DISMISSABLE,
    RETRYABLE,
    REVIEWABLE,
    answer_fits,
    discard_work,
    record_answers,
    requeue,
)
from ep.importers.proposal import Answer, Choice, LogEntry, Proposal
from ep.jobs.queue import create_import, enqueue_import
from ep.media_roots import is_within_media_roots
from ep.metadata import bandcamp
from ep.metadata.ytdlp import youtube_url
from ep.models import ImportJob, ImportKind, JobState
from ep.settings import Settings

router = APIRouter(prefix="/imports", tags=["imports"])


def _proposal(job: ImportJob) -> Proposal | None:
    return Proposal.model_validate(job.proposal) if job.proposal is not None else None


def _summary_fields(job: ImportJob, proposal: Proposal | None) -> dict:
    open_issues = len(proposal.open_issues()) if proposal else 0
    actions = []
    if job.state in REVIEWABLE and open_issues:
        actions.append(JobAction.review)
    if job.state in RETRYABLE:
        actions.append(JobAction.retry)
    if job.state in DISMISSABLE:
        actions.append(JobAction.dismiss)
    return {
        "id": job.id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "kind": job.kind,
        "input": job.input,
        "state": job.state,
        "progress": ProgressRead(stage=job.stage, done=job.progress_done, total=job.progress_total),
        "error": job.error,
        "open_issues": open_issues,
        "actions": actions,
    }


def job_summary(job: ImportJob) -> ImportJobSummary:
    return ImportJobSummary(**_summary_fields(job, _proposal(job)))


def job_read(job: ImportJob) -> ImportJobRead:
    proposal = _proposal(job)
    return ImportJobRead(
        **_summary_fields(job, proposal),
        log=[LogEntry.model_validate(entry) for entry in job.log],
        proposal=proposal,
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
    """The canonical URL for a Bandcamp or YouTube import (the job's input)."""
    match kind:
        case ImportKind.bandcamp:
            canonical = bandcamp.canonical_url(url)
            expected = "a Bandcamp album or track page URL (https://<name>.bandcamp.com/album/...)"
        case ImportKind.youtube:
            parsed = youtube_url(url)
            canonical = parsed.url if parsed else None
            expected = "a YouTube video or playlist URL"
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
    single-file album) or a playlist (one release group), downloaded into the managed
    library. URLs are stored canonical (tracking parameters dropped)."""
    if body.kind is ImportKind.folder:
        job_input = str(check_folder_input(body.input, settings, ("input",)))
    else:
        job_input = check_url_input(body.kind, body.input, ("input",))
    job = create_import(session, body.kind, job_input)
    session.commit()
    return job_read(job)


@router.get("")
def list_import_jobs(
    session: SessionDep,
    page: PageDep,
    state: Annotated[list[JobState] | None, Query()] = None,
    kind: ImportKind | None = None,
) -> list[ImportJobSummary]:
    """Jobs, newest first. The inbox: ``state=needs_review&state=failed``."""
    statement = select(ImportJob)
    if state:
        statement = statement.where(col(ImportJob.state).in_(state))
    if kind:
        statement = statement.where(ImportJob.kind == kind)
    jobs = session.exec(
        statement.order_by(col(ImportJob.created_at).desc(), col(ImportJob.id))
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [job_summary(job) for job in jobs]


@router.get("/{id}", responses=NOT_FOUND)
def get_import_job(id: uuid.UUID, session: SessionDep) -> ImportJobRead:
    """State, progress, log and proposal (with its issues) of a job."""
    return job_read(get_or_404(session, ImportJob, id))


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
def review_import_job(id: uuid.UUID, body: ReviewRequest, session: SessionDep) -> ImportJobRead:
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
            else answer_fits(issue, answer)
        )
        if not fits:
            message = f"not one of the issue's choices ({', '.join(issue.choices)})"
            if answer.choice is Choice.pick_candidate:
                message = "candidate_id must be the id of one of the issue's candidates"
            raise unprocessable(
                ("answers", index), message, given.model_dump(mode="json"), "value_error"
            )
        answers[issue.id] = answer
    record_answers(job, answers)
    enqueue_import(session, job.id)
    session.commit()
    return job_read(job)


@router.post("/{id}/retry", responses=NOT_FOUND | CONFLICT)
def retry_import_job(id: uuid.UUID, session: SessionDep) -> ImportJobRead:
    """Run a ``needs_review``, ``failed`` or ``done`` job again from the start, with its
    answers. A done job creates nothing new: files already imported are left out."""
    job = _job_in(session, id, RETRYABLE, "retried")
    requeue(job)
    enqueue_import(session, job.id)
    session.commit()
    return job_read(job)


@router.post("/{id}/dismiss", responses=NOT_FOUND | CONFLICT)
def dismiss_import_job(id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> ImportJobRead:
    """Take a ``queued``, ``needs_review`` or ``failed`` job out of the inbox. Nothing is
    imported, and its downloads are deleted; the watcher does not queue the folder again
    until it changes."""
    job = _job_in(session, id, DISMISSABLE, "dismissed")
    job.state = JobState.dismissed
    session.commit()
    discard_work(settings.library_dir, job.id)
    return job_read(job)
