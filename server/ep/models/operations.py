"""Operations: import jobs and watched directories (see plans/backend.md, "Operations").

An ``import_job`` is one run of an importer (``ep.importers``) over one input (a folder
path, later a URL). It is also an entry of the user's decision inbox: a job with a
problem stays in ``needs_review`` or ``failed`` with its issues in ``proposal`` until it
is completed or explicitly dismissed. The job queue itself (Procrastinate, ``ep.jobs``)
only carries "run import job <id>"; everything the user sees is here.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from ep.models.base import Base
from ep.models.catalogue import enum_column


class ImportKind(StrEnum):
    folder = "folder"
    bandcamp = "bandcamp"
    youtube = "youtube"
    nts = "nts"


class JobState(StrEnum):
    # Waiting for the worker.
    queued = "queued"
    # Fetching, resolving metadata, matching: building the proposal.
    running = "running"
    # The proposal has open issues: waiting for the user's answers.
    needs_review = "needs_review"
    # Writing the proposal to the library (one transaction).
    applying = "applying"
    done = "done"
    # Stopped by an error (``error``); can be retried.
    failed = "failed"
    # Removed from the inbox by the user.
    dismissed = "dismissed"


class JobStage(StrEnum):
    """The pipeline stage a job is in (or stopped in), for progress."""

    fetch = "fetch"
    resolve = "resolve"
    match = "match"
    propose = "propose"
    download = "download"
    apply = "apply"


class ImportJob(Base, table=True):
    __tablename__ = "import_job"
    __table_args__ = (
        # The inbox: jobs by state.
        sa.Index("ix_import_job_state", "state"),
        # "Is this folder already queued or imported" (the watcher).
        sa.Index("ix_import_job_kind_input", "kind", "input"),
    )

    kind: ImportKind = Field(sa_type=enum_column(ImportKind))
    # A directory path for ``folder``; a URL for the other kinds.
    input: str
    state: JobState = Field(default=JobState.queued, sa_type=enum_column(JobState))
    stage: JobStage | None = Field(default=None, sa_type=enum_column(JobStage))
    # Items of the current stage done so far, out of ``progress_total`` (if known).
    progress_done: int = 0
    progress_total: int | None = None
    # ``ep.importers.proposal.Proposal`` as JSON: what applying will do, and the issues.
    proposal: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    # The user's answers to issues: issue id -> ``ep.importers.proposal.Answer`` as JSON.
    answers: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    # Append-only list of ``ep.importers.proposal.LogEntry`` as JSON.
    log: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    error: str | None = None


class WatchDir(Base, table=True):
    __tablename__ = "watch_dir"

    # Absolute path as the server sees it (e.g. /watch/Library).
    path: str = Field(unique=True)
    enabled: bool = True
    # Folders that last changed before this are not imported automatically (only by
    # an explicit scan): registering a directory does not import what is already there.
    baseline_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
