"""Recovery boundaries without a database or a browser process."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app import worker
from app.apply import manual, run, submit
from app.jobs.service import JobBusyError
from tests.apply.conftest import AT


def test_busy_manual_startup_cannot_rollback_other_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    committed: list[str] = []
    pending: list[str] = []

    def transaction(sessions, step):
        pending.clear()
        try:
            result = step(None)
        except JobBusyError:
            pending.clear()
            raise
        committed.extend(pending)
        return result

    def reset(name):
        def step(db, now):
            pending.append(name)
            return 1

        return step

    def busy(db, now):
        raise JobBusyError

    monkeypatch.setattr(run, "_in_db", transaction)
    monkeypatch.setattr(run, "reset_interrupted", reset("fill"))
    monkeypatch.setattr(submit, "reset_interrupted", reset("submit_unknown"))
    monkeypatch.setattr(manual, "reset_interrupted", busy)
    monkeypatch.setattr(worker, "sessions_of", lambda ctx: None)
    monkeypatch.setattr(worker, "clock_of", lambda ctx: lambda: AT)
    with pytest.raises(JobBusyError):
        asyncio.run(worker.reset_fills_at_start({}))
    assert committed == ["fill", "submit_unknown"]


def test_exhausted_close_keeps_a_durable_recovery_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[tuple[uuid.UUID, str, bool]] = []
    application_id = uuid.uuid4()

    def busy(*args, **kwargs):
        raise JobBusyError

    def mark(db, wanted, code, now, *, seen=False):
        marked.append((wanted, code, seen))

    monkeypatch.setattr(run, "_in_db", lambda sessions, step: step(None))
    monkeypatch.setattr(manual, "close", busy)
    monkeypatch.setattr(manual, "mark_ended", mark, raising=False)
    fill = SimpleNamespace(sessions=None, clock=lambda: AT)
    assert (
        asyncio.run(manual._close(fill, application_id, "interrupted", True)) == "recovery_pending"
    )
    assert marked == [(application_id, "interrupted", True)]
