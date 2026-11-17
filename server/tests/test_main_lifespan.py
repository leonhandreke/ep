"""``ep.main.lifespan`` with ``EP_RUN_WORKER``: a worker that stops on its own stops
the server (logged), instead of leaving an API with nothing importing."""

import asyncio
import logging
import signal

import pytest

import ep.jobs.worker
import ep.main
from ep.settings import Settings


def _run_lifespan(monkeypatch: pytest.MonkeyPatch, worker_run, *, cancel: bool) -> list[int]:
    raised: list[int] = []
    monkeypatch.setattr(ep.main, "get_settings", lambda: Settings(run_worker=True))
    monkeypatch.setattr(ep.jobs.worker, "run", worker_run)
    monkeypatch.setattr(ep.main.signal, "raise_signal", raised.append)

    async def serve() -> None:
        async with ep.main.lifespan(ep.main.app):
            if not cancel:
                for _ in range(5):  # let the worker task run and finish
                    await asyncio.sleep(0)

    asyncio.run(serve())
    return raised


def test_worker_failure_stops_the_server(monkeypatch, caplog):
    async def failing_run(**_: object) -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.CRITICAL, logger="ep.main"):
        raised = _run_lifespan(monkeypatch, failing_run, cancel=False)
    assert raised == [signal.SIGTERM]
    assert "the import worker failed" in caplog.text
    assert "boom" in caplog.text


def test_worker_returning_stops_the_server(monkeypatch, caplog):
    async def returning_run(**_: object) -> None:
        return None

    with caplog.at_level(logging.CRITICAL, logger="ep.main"):
        raised = _run_lifespan(monkeypatch, returning_run, cancel=False)
    assert raised == [signal.SIGTERM]
    assert "the import worker stopped" in caplog.text


def test_shutdown_cancelling_the_worker_does_not_signal(monkeypatch):
    async def forever(**_: object) -> None:
        await asyncio.Event().wait()

    assert _run_lifespan(monkeypatch, forever, cancel=True) == []
