"""The live text at the end of a run, and what counts as slow (issue #155, point 4), on the
worker's side. Once the live text is closed no write starts: a pump the run could not stop
within `pump_stop_s` writes nothing more when its call comes back, and what that call wrote,
which lands after the final clear, is cleared at once. And an attempt is slow on its own calls,
not on its wait for the lock. Memory stores, the test's clock for the slow mark, and a real
Postgres for the run."""

import asyncio
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import live, runner
from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import add_user, context, new_run, run_row, save_credential
from tests.agents.test_live_text import delta, start
from tests.conftest import make_settings

RUN = uuid.UUID("00000000-0000-4000-8000-000000000155")


class Behind(RecordingStore):
    """A store whose first call named `held` neither answers nor lets itself be cancelled until
    `free`; then what it writes lands, and it returns, or raises `then`. Every call is noted in
    `began` when it begins; what lands is in `ops`."""

    def __init__(self, held: str, then: BaseException | None = None) -> None:
        super().__init__()
        self.held = held
        self.then = then
        self.in_flight = asyncio.Event()
        self.free = asyncio.Event()
        self.began: list[str] = []

    async def _wait(self, name: str) -> bool:
        self.began.append(name)
        if name != self.held or self.in_flight.is_set():
            return False
        self.in_flight.set()
        while not self.free.is_set():
            try:
                await self.free.wait()
            except asyncio.CancelledError:
                continue
        return True

    def _after(self, late: bool) -> None:
        if late and self.then is not None:
            raise self.then

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        late = await self._wait("open")
        await super().open(run_id, block, after, previous)
        self._after(late)

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        late = await self._wait("append")
        await super().append(run_id, block, text)
        self._after(late)

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        late = await self._wait("clear")
        await super().clear(run_id, block)
        self._after(late)


# --- (a) nothing lands after the final clear ----------------------------------------------------

LATE = {
    "an append that returns": ("append", None),
    "an open that returns": ("open", None),
    "an append that then fails": ("append", ConnectionError("connection reset")),
    "an append that then says cancelled": ("append", asyncio.CancelledError()),
}


@pytest.mark.parametrize(("held", "then"), list(LATE.values()), ids=list(LATE))
def test_a_call_that_comes_back_after_the_final_clear_leaves_nothing_behind(
    held: str, then: BaseException | None
) -> None:
    """The runner cancels the pump, waits for it at most `pump_stop_s`, records the end and
    closes the live text (the final clear). Here the pump's call ignores all of it and comes
    back afterwards, so what it wrote lands after the final clear. Before issue #155 the pump
    then went on writing (an open is followed by its append), and only the expiry of the
    buffer removed it."""

    async def go() -> tuple[Behind, int, int, asyncio.Task[None]]:
        store = Behind(held, then)
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0, timeout_s=0.05, close_retry_s=0.01)
        pump = asyncio.create_task(writer.pump())
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "an answer cut by the end of the run"))
        await store.in_flight.wait()
        pump.cancel()
        stopped, _ = await asyncio.wait({pump}, timeout=0.05)  # the runner's bound
        assert not stopped
        await writer.aclose()  # the final clear, while the pump's call is under way
        closed, landed = len(store.began), len(store.ops)
        store.free.set()  # the call comes back
        await asyncio.wait_for(asyncio.gather(pump, return_exceptions=True), timeout=5)
        return store, closed, landed, pump

    store, closed, landed, pump = asyncio.run(go())
    assert store.ops[landed][0] == held  # what the late call wrote landed after the close
    assert store.began[closed:] == ["clear"]  # then nothing but one clear began
    assert store.ops[-1][0] == "clear"
    assert (store.meta, store.text) == ({}, {})
    assert pump.done()  # the pump ended by itself


def test_once_closed_the_live_text_writes_nothing_and_its_pump_ends() -> None:
    async def go() -> tuple[RecordingStore, list[tuple[Any, ...]], asyncio.Task[None]]:
        store = RecordingStore()
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0)
        pump = asyncio.create_task(writer.pump())
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one"))
        while not store.text:  # noqa: ASYNC110
            await asyncio.sleep(0)
        await writer.aclose()
        await asyncio.wait_for(pump, timeout=5)  # not cancelled: it ends once closed
        ops = list(store.ops)
        # Stream events after the end, and every way of asking for a write.
        writer.feed(start(1, text="after the end"))
        writer.feed(delta(1, ", and more"))
        await writer._sync()
        await writer.close_block()
        await writer.aclose()
        return store, ops, pump

    store, ops, pump = asyncio.run(go())
    assert [op[0] for op in ops] == ["open", "append", "clear"]
    assert store.ops == ops  # nothing after the close
    assert (store.meta, store.text) == ({}, {})
    assert not pump.cancelled()


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


def test_a_run_whose_pump_did_not_stop_leaves_no_live_text_once_the_call_comes_back(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same through the runner: its end does not wait for the pump (issue #90), and the
    open the pump was in lands once the run has ended, after the final clear."""
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    run_id = new_run(factory, user, {"message": "hello"})
    pumps: list[asyncio.Task[Any]] = []
    pump = live.LiveText.pump

    async def spy_pump(self: live.LiveText) -> None:
        task = asyncio.current_task()
        assert task is not None
        pumps.append(task)
        await pump(self)

    monkeypatch.setattr(live.LiveText, "pump", spy_pump)

    async def go() -> tuple[RunStatus, Behind, int, int]:
        store = Behind("open")
        rc = context(settings, factory, keyring, lambda: FakeBackend(speed=0.0))
        rc = replace(rc, live_store=store, live_flush_s=0.0, live_timeout_s=0.05, pump_stop_s=0.05)
        status = await asyncio.wait_for(runner.execute(rc, run_id), timeout=20)
        closed, landed = len(store.began), len(store.ops)
        store.free.set()
        await asyncio.wait_for(asyncio.gather(*pumps, return_exceptions=True), timeout=5)
        return status, store, closed, landed

    with caplog.at_level(logging.WARNING):
        status, store, closed, landed = asyncio.run(go())
    assert status is RunStatus.done
    assert run_row(factory, run_id).status is RunStatus.done
    assert caplog.text.count("live text pump did not stop") == 1
    assert store.began[:closed] == ["open"]  # the one call of the run, held to its end
    assert store.ops[landed][0] == "open"  # it landed after the end
    assert store.began[closed:] == ["clear"]
    assert (store.meta, store.text) == ({}, {})
    assert [task.done() for task in pumps] == [True]


# --- (b) slow is the store's own time -----------------------------------------------------------


class Timed(RecordingStore):
    """A store whose calls take `takes[name]` seconds of the test's clock. The first call named
    `gate_on` waits for `gate` first, while the test moves the clock."""

    def __init__(self, now: list[float], takes: dict[str, float], gate_on: str) -> None:
        super().__init__()
        self.now = now
        self.takes = takes
        self.gate_on = gate_on
        self.gate = asyncio.Event()
        self.waiting = asyncio.Event()

    async def _take(self, name: str) -> None:
        if name == self.gate_on and not self.waiting.is_set():
            self.waiting.set()
            await self.gate.wait()
        self.now[0] += self.takes.get(name, 0.0)

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        await self._take("open")
        await super().open(run_id, block, after, previous)

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        await self._take("append")
        await super().append(run_id, block, text)

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        await self._take("clear")
        await super().clear(run_id, block)


# The pump's attempt takes `first` seconds; the runner's close of the block waits for it behind
# the lock, then its own clear takes `own`. The mark is 0.5 s.
QUEUED = {
    "neither slow, 0.6 s in all": (0.4, 0.2, 0, ["open", "append", "clear"]),
    "the queued call's own calls are slow": (0.3, 0.6, 1, ["open", "append", "clear"]),
    "the first is slow, the queued one waits out the back off": (0.7, 0.2, 1, ["open", "append"]),
}


@pytest.mark.parametrize(("first", "own", "slow", "calls"), list(QUEUED.values()), ids=list(QUEUED))
def test_a_call_queued_behind_another_is_slow_only_for_its_own_calls(
    first: float, own: float, slow: int, calls: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    """Before issue #155 `slow` was measured from the start of the call, its wait for the lock
    included: the first row was counted slow although no call of it took 0.5 s."""

    async def go() -> tuple[live.LiveText, Timed]:
        now = [1000.0]
        store = Timed(now, {"clear": own}, gate_on="append")
        writer = live.LiveText(
            store, RUN, lambda: 3, flush_s=0, timeout_s=1.0, slow_s=0.5, backoff_s=5,
            clock=lambda: now[0],
        )  # fmt: skip
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one"))
        pump = asyncio.create_task(writer._sync())  # the pump's attempt, held in its append
        await store.waiting.wait()
        closing = asyncio.create_task(writer.close_block())  # the runner, behind the lock
        for _ in range(20):
            await asyncio.sleep(0)
        assert not closing.done()
        now[0] += first
        store.gate.set()
        await asyncio.gather(pump, closing)
        return writer, store

    with caplog.at_level(logging.WARNING):
        writer, store = asyncio.run(go())
    assert [op[0] for op in store.ops] == calls
    assert writer.slow == slow
    assert writer.failures == 0
    assert caplog.text.count("live text slow") == slow
