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


def test_close_requests_a_final_watch_before_the_durable_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    application_id = uuid.uuid4()
    live = manual.Live(application_id, "lease-1")

    async def watch(*args):
        order.append("watch")
        live.seen = True

    def cancel(*args):
        order.append("close")
        assert live.seen
        return SimpleNamespace(state=SimpleNamespace(value="submitted"))

    class Channel:
        async def pop(self, *args):
            return None

        async def put_status(self, *args):
            pass

    clock = iter([0, 1, 2, 3, 4, 5, 6])
    monkeypatch.setattr(run, "_in_db", lambda sessions, step: step(None))
    monkeypatch.setattr(manual, "close_requested", lambda *args: True, raising=False)
    monkeypatch.setattr(manual, "is_open", lambda *args: True)
    monkeypatch.setattr(manual, "_watch", watch)
    monkeypatch.setattr(manual, "cancel", cancel)
    fill = SimpleNamespace(sessions=None, clock=lambda: AT)
    svc = SimpleNamespace(fill=fill, channel=Channel())
    plan = SimpleNamespace(application_id=application_id, user_id=uuid.uuid4())
    result = asyncio.run(manual._loop(svc, None, None, plan, live, 3, lambda: next(clock)))
    assert result == "submitted"
    assert order == ["watch", "close"]


@pytest.mark.parametrize("ended", [[], [uuid.uuid4()]])
def test_the_tick_writes_ended_manual_sessions_before_it_queues(
    monkeypatch: pytest.MonkeyPatch, ended: list[uuid.UUID]
) -> None:
    order: list[str] = []
    given = SimpleNamespace(name="services")

    class Sessions:
        def __enter__(self):
            return None

        def __exit__(self, *args):
            return False

    async def recover(fill, ids):
        order.append("recover")
        assert (fill, ids) == (given, ended)
        return len(ids)

    def due(db, *args):
        order.append("due")
        return []

    monkeypatch.setattr(worker, "sessions_of", lambda ctx: Sessions)
    monkeypatch.setattr(manual, "ended_ids", lambda db: list(ended))
    monkeypatch.setattr(manual, "recover_ended", recover)
    monkeypatch.setattr(run, "due", due)
    monkeypatch.setattr(submit, "due", lambda db: [])
    monkeypatch.setattr(submit, "undecided_of", lambda db, approved: set())
    assert asyncio.run(worker.apply_tick({"apply_services": given})) == 0
    assert order == (["recover", "due"] if ended else ["due"])
