"""Hardening of the live answer text after its reviews (issue #80, points 2, 3, 4 and 6), on the
worker's side: an agent that turns its live text off publishes nothing, a sub agent's stream
events are never published, a store that never answers does not slow a run down, and three
robustness points of `LiveText` (a block index that is not a whole number, a pump cancelled in
the middle of a write, a clear that fails at the end of the run). Memory stores and a real
Postgres; the same points against a real Redis are in test_live_stream.py."""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import StreamEvent
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, live, runner
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    Backends,
    add_user,
    context,
    events,
    execute,
    new_run,
    run_row,
    save_credential,
)
from tests.agents.test_live_runner import assistant, live_context, result, script, stored, text
from tests.agents.test_live_text import delta, start
from tests.conftest import make_settings

__all__ = ["script"]

RUN = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


class Hung(RecordingStore):
    """A store that never answers: every call waits until it is cancelled. It keeps what a bound
    on its cost needs, and no clock: how many calls and, when given `status`, the status of the
    run at the moment of each call."""

    def __init__(self, status: Callable[[uuid.UUID], RunStatus] | None = None) -> None:
        super().__init__()
        self.calls = 0
        self.statuses: list[RunStatus] = []
        self._status = status

    async def _never(self, run_id: uuid.UUID) -> None:
        self.calls += 1
        if self._status is not None:
            self.statuses.append(self._status(run_id))
        await asyncio.Event().wait()

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        await self._never(run_id)

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        await self._never(run_id)

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        await self._never(run_id)


# --- point 2: the per agent switch --------------------------------------------------------------


def test_every_agent_is_live_unless_its_definition_says_otherwise() -> None:
    # The agents of definitions.py are live; an agent of another module registered in this
    # process may turn live text off only together with private_io (documents D7's writer).
    assert all(
        agent.live_text for agent in (definitions.ECHO, definitions.TRIAGE, definitions.JOB_CHAT)
    )
    assert all(agent.live_text or agent.private_io for agent in definitions.AGENTS.values())
    assert definitions.live_text("echo")
    assert not definitions.live_text("an agent that is gone")
    assert not replace(definitions.ECHO, live_text=False).live_text


def test_an_agent_with_live_text_off_asks_for_no_partial_messages_and_publishes_nothing(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer = "An answer about a private document. " * 30
    name = script("private", [assistant(text(answer)), result(answer)], chars=7)
    found = {}
    for label, changes in (("on", {}), ("off", {"live_text": False})):
        store = RecordingStore()
        backends = Backends(speed=0.0)
        rc = context(settings, factory, keyring, backends, max_turns=4, **changes)
        rc = replace(rc, live_store=store, live_flush_s=0.0)
        run_id = new_run(factory, alice_id, {"message": "x", "script": name})
        with caplog.at_level(logging.INFO):
            assert execute(rc, run_id) is RunStatus.done
        found[label] = (store, backends.made[0].seen_options, stored(factory, run_id))
        finished = [r for r in caplog.records if r.getMessage() == "agent run finished"][-1]
        assert (finished.live_deltas is None) == (label == "off")
    on, off = found["on"], found["off"]
    assert on[1].include_partial_messages is True
    assert off[1].include_partial_messages is False  # Claude Code is not even asked
    assert on[0].written() == {1: answer}  # the control: the same run is live when allowed
    assert off[0].ops == []
    assert off[0].writes == 0  # no call at all reached the store, not even a clear
    assert off[2] == on[2]  # and what is stored is the same


# --- point 3: sub agents -------------------------------------------------------------------------


def sub_event(event: dict[str, Any], parent: str | None) -> StreamEvent:
    return StreamEvent(
        uuid=str(uuid.uuid4()), session_id="s", event=event, parent_tool_use_id=parent
    )


def test_stream_events_of_a_sub_agent_are_never_published() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        state = runner._State()
        state.live_text = live.LiveText(store, RUN, lambda: 3, flush_s=0)
        recorder: Any = None  # a StreamEvent stores nothing
        for event in (start(0, text=""), delta(0, "SUB AGENT TEXT")):
            await runner._handle(sub_event(event, "toolu_parent"), state, recorder)
        await state.live_text._sync()
        assert store.ops == []
        assert state.live_text.block == 0
        # The run's own text, with a sub agent's deltas in between on the same block index.
        await runner._handle(sub_event(start(0, text=""), None), state, recorder)
        await runner._handle(sub_event(delta(0, "mine, "), None), state, recorder)
        await runner._handle(sub_event(delta(0, "NOT MINE"), "toolu_parent"), state, recorder)
        await runner._handle(sub_event({"type": "message_start"}, "toolu_parent"), state, recorder)
        await runner._handle(sub_event(delta(0, "all mine"), None), state, recorder)
        await state.live_text._sync()
        return store

    assert asyncio.run(go()).written() == {1: "mine, all mine"}


def test_a_run_whose_stream_holds_sub_agent_events_publishes_only_its_own_text(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    def event(body: dict[str, Any], parent: str | None) -> dict[str, Any]:
        return {
            "message": {
                "type": "stream_event",
                "uuid": str(uuid.uuid4()),
                "session_id": "{session_id}",
                "parent_tool_use_id": parent,
                "event": body,
            }
        }

    steps = [
        event({"type": "message_start", "message": {}}, None),
        event(start(0, text=""), None),
        event(delta(0, "The answer"), None),
        event(start(0, text=""), "toolu_sub"),
        event(delta(0, "WHAT THE SUB AGENT WRITES"), "toolu_sub"),
        {"sleep": 0.2},
        event(delta(0, " is here."), None),
        {"sleep": 0.2},
        assistant(text("The answer is here.")),
        event({"type": "content_block_stop", "index": 0}, None),
        result("The answer is here."),
    ]
    name = script("subagent", steps)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    rc = replace(live_context(settings, factory, keyring, store, speed=1.0), live_flush_s=0.0)
    assert execute(rc, run_id) is RunStatus.done
    assert store.written() == {1: "The answer is here."}
    assert "SUB AGENT" not in store.everything()
    assert [data for kind, data in events(factory, run_id) if kind == "text"] == [
        {"text": "The answer is here."}
    ]


# --- point 4: a store that never answers --------------------------------------------------------


def test_a_store_that_never_answers_costs_one_timeout_per_back_off() -> None:
    """The runner waits for the store in `close_block` only. With a store that never answers:
    the first attempt takes the timeout, then the store is left alone and every `close_block`
    returns at once, until the back off has passed; each failure in a row doubles it, up to
    the maximum."""
    timeout = 0.05

    async def go() -> None:
        store = Hung()
        now = [1000.0]
        writer = live.LiveText(
            store,
            RUN,
            lambda: 3,
            flush_s=0,
            timeout_s=timeout,
            backoff_s=5,
            backoff_max_s=20,
            clock=lambda: now[0],
        )

        async def block() -> float:
            writer.feed(start(0, text=""))
            writer.feed(delta(0, "some text"))
            # The pump is writing the block when its complete message arrives: the runner
            # waits behind that write (the worst case for the run).
            pump = asyncio.create_task(writer._sync())
            await asyncio.sleep(0)
            began = time.perf_counter()
            await writer.close_block()
            took = time.perf_counter() - began
            await pump
            return took

        # What each wait is, in the order the store is asked: 5, 10, 20, 20 seconds.
        for wait in (5, 10, 20, 20):
            before = store.calls
            assert timeout <= await block() < timeout + 0.5  # one attempt: the timeout
            assert store.calls == before + 1
            for _ in range(50):  # then left alone: fifty blocks, not one call
                assert await block() < 0.02
            now[0] += wait - 0.1
            assert await block() < 0.02  # not yet
            assert store.calls == before + 1
            now[0] += 0.2
        assert writer.failures == 4
        assert writer.attempts == 4

    asyncio.run(go())


def test_the_back_off_starts_over_after_a_success_and_the_next_block_is_published_whole() -> None:
    async def go() -> None:
        store = RecordingStore()
        now = [1000.0]
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0, backoff_s=5, clock=lambda: now[0])
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one "))
        await writer._sync()
        store.fail = ConnectionError("down")
        writer.feed(delta(0, "two "))
        await writer._sync()  # fails: block 1 is given up, its clear is owed
        store.fail = None
        await writer.close_block()  # left alone: the broken buffer is still there
        assert store.written() == {1: "one "}
        assert RUN in store.meta
        # A block that begins and grows during the back off...
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "three "))
        await writer._sync()
        assert writer.attempts == 2
        now[0] += 5.1
        writer.feed(delta(0, "four"))
        await writer._sync()
        # ...is published whole once it is over: the owed clear first, then all of its text.
        assert store.ops[-3:] == [("clear", 1), ("open", 2, 3), ("append", 2, "three four")]
        # A failure after a success waits the first back off again, not the doubled one.
        store.fail = ConnectionError("down")
        writer.feed(delta(0, " five"))
        await writer._sync()
        store.fail = None
        now[0] += 5.1
        await writer.close_block()
        assert store.meta == {}
        assert store.text == {}
        assert writer.failures == 2

    asyncio.run(go())


class Late(RecordingStore):
    """A store that answers, late: every call takes `takes` seconds of the test's clock."""

    def __init__(self, now: list[float], takes: float) -> None:
        super().__init__()
        self.now = now
        self.takes = takes
        self.calls = 0

    def _late(self) -> None:
        self.calls += 1
        self.now[0] += self.takes

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        self._late()
        await super().open(run_id, block, after, previous)

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        self._late()
        await super().append(run_id, block, text)

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        self._late()
        await super().clear(run_id, block)


def test_a_store_that_answers_slowly_is_left_alone_like_one_that_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #90, point 4. Each call answers in 0.4 s, under the timeout of 1 s, so nothing
    ever fails: before, every block of every run paid for it. An attempt that took more than
    `slow_s` now starts the back off of a failure, and nothing is given up."""

    async def go() -> None:
        now = [1000.0]
        store = Late(now, 0.4)
        writer = live.LiveText(
            store, RUN, lambda: 3, flush_s=0, timeout_s=1.0, slow_s=0.5, backoff_s=5,
            clock=lambda: now[0],
        )  # fmt: skip
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one "))
        await writer._sync()  # open and append: 0.8 s in all
        assert (writer.attempts, writer.slow, writer.failures) == (1, 1, 0)
        # Nothing is given up: the store holds what it was told, and the block is not broken.
        assert store.ops == [("open", 1, 3), ("append", 1, "one ")]
        # Left alone: twenty blocks, each closed by the runner, and not one call.
        for _ in range(20):
            writer.feed(delta(0, "more "))
            await writer._sync()
            await writer.close_block()
            writer.feed(start(0, text="next "))
        assert store.calls == 2
        now[0] += 4.9
        await writer.close_block()
        assert store.calls == 2  # not yet
        # Afterwards the block being written is published whole, in place of the old one.
        now[0] += 0.2
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "twenty two"))
        await writer._sync()
        assert store.ops[2:] == [("open", 22, 3), ("append", 22, "twenty two")]
        assert list(store.text) == [(RUN, 22)]
        # Slow again: the back off doubles, as it does for failures in a row.
        assert writer.slow == 2
        now[0] += 5.1
        await writer.close_block()
        assert store.calls == 4
        now[0] += 5.0
        store.takes = 0.0  # the store is well again
        await writer.close_block()
        assert (store.meta, store.text) == ({}, {})
        # A quick answer is no reason to wait: the next block is published at once.
        writer.feed(start(0, text="quick"))
        await writer._sync()
        assert store.ops[-2:] == [("open", 23, 3), ("append", 23, "quick")]
        assert (writer.slow, writer.failures) == (2, 0)

    with caplog.at_level(logging.WARNING):
        asyncio.run(go())
    assert caplog.text.count("live text slow") == 1  # the first one is logged, the rest counted


def test_an_answer_under_the_slow_mark_starts_no_back_off() -> None:
    async def go() -> None:
        now = [1000.0]
        store = Late(now, 0.2)  # open and append: 0.4 s, under the mark of 0.5 s
        writer = live.LiveText(
            store, RUN, lambda: 3, flush_s=0, timeout_s=1.0, slow_s=0.5, clock=lambda: now[0]
        )
        writer.feed(start(0, text=""))
        for number in range(5):
            writer.feed(delta(0, f"{number} "))
            await writer._sync()
        assert store.calls == 1 + 5
        assert (writer.slow, writer.failures) == (0, 0)
        assert live.LiveText(store, RUN, lambda: 3, timeout_s=0.3)._slow_s == 0.3
        assert live.LiveText(store, RUN, lambda: 3)._slow_s == live.STORE_SLOW_S == 0.5

    asyncio.run(go())


def test_the_wait_for_an_attempt_under_way_comes_out_of_the_timeout_of_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #90, point 4: `close_block` could cost two timeouts, one waiting for the pump's
    attempt to end and one for its own. Its own attempt now gets what the wait left."""

    async def go() -> list[float]:
        now = [1000.0]
        gate = asyncio.Event()

        class Held(RecordingStore):
            async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
                await gate.wait()
                await super().append(run_id, block, text)

        store = Held()
        given: list[float] = []
        real = asyncio.timeout

        def timeout(delay: float | None) -> asyncio.Timeout:
            assert delay is not None
            given.append(delay)
            return real(delay)

        monkeypatch.setattr(live.asyncio, "timeout", timeout)
        writer = live.LiveText(
            store, RUN, lambda: 3, flush_s=0, timeout_s=1.0, slow_s=1.0, clock=lambda: now[0]
        )
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one"))
        pump = asyncio.create_task(writer._sync())  # the pump's attempt, held in its append
        for _ in range(20):
            await asyncio.sleep(0)
        assert store.ops == [("open", 1, 3)]
        closing = asyncio.create_task(writer.close_block())  # the runner, after the block
        for _ in range(20):
            await asyncio.sleep(0)
        assert not closing.done()
        now[0] += 0.3  # what the runner has waited when the pump's attempt ends
        gate.set()
        await asyncio.gather(pump, closing)
        assert (store.meta, store.text) == ({}, {})
        return given

    given = asyncio.run(go())
    # The pump: the store's lock, then its attempt. The runner: the lock, then what is left.
    assert given[:3] == [1.0, 1.0, 1.0]
    assert given[3] == pytest.approx(0.7)
    assert len(given) == 4


def test_a_run_on_a_store_that_never_answers_keeps_its_speed(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Six text blocks, a store that never answers, a timeout of 0.3 s. The stated bound: the
    run itself (claimed to final status) waits for the store at most one timeout per attempt,
    and with a back off longer than the run there is one attempt; the end of the run, after
    its status is stored, adds at most two timeouts and the retry pause. Before issue #80 the
    same run waited up to two timeouts per block: 3.6 s here. The bound is held by the number
    of times the store was called, never by a clock: durations on a loaded machine prove
    nothing (issue #90, point 5). The store also notes the status of the run at each call, so
    the order is held too: one attempt while the run is running, then the end of the run. The
    back off is set far above what a run can last on a starved machine, so that the single
    attempt of a running run is not decided by a clock either (issue #104)."""
    timeout, blocks = 0.3, 6
    said = [assistant(text(f"Part {number} of the answer. " * 4)) for number in range(blocks)]
    name = script("sixblocks", [*said, result("Done.")], chars=40, sleep=0.0)

    def run(store: live.LiveStore | None) -> uuid.UUID:
        run_id = new_run(factory, alice_id, {"message": "x", "script": name})
        rc = context(settings, factory, keyring, Backends(speed=0.0), max_turns=4)
        rc = replace(
            rc,
            live_store=store,
            live_flush_s=0.0,
            live_timeout_s=timeout,
            live_backoff_s=live.WRITE_BACKOFF_MAX_S,
        )
        assert execute(rc, run_id) is RunStatus.done
        assert run_row(factory, run_id).finished_at is not None
        return run_id

    plain_id = run(None)
    hung = Hung(status=lambda run_id: run_row(factory, run_id).status)
    with caplog.at_level(logging.WARNING):
        hung_id = run(hung)
    # One attempt in the run, two at its end: each waits one timeout at most, so the whole
    # cost is three timeouts whatever the number of blocks.
    assert 1 <= hung.calls <= 1 + 2
    # And in that order: one attempt while the run is running, the others once its status is
    # stored. Every call noted a status, so none was made that this list does not hold.
    assert hung.statuses[0] is RunStatus.running
    assert hung.statuses[1:] in ([RunStatus.done], [RunStatus.done] * 2)
    assert hung.calls == len(hung.statuses)
    assert stored(factory, hung_id) == stored(factory, plain_id)  # and the run is the same
    assert caplog.text.count("live text not published") == 1


# --- point 6: LiveText robustness ----------------------------------------------------------------


def test_a_block_index_that_is_true_is_not_index_one() -> None:
    """Python's True equals 1 and is an int: a delta whose index is `true` must not be read as
    a delta of block 1, and a block that starts with such an index takes no delta at all."""

    async def go() -> RecordingStore:
        store = RecordingStore()
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0)
        writer.feed(start(1, text=""))
        writer.feed(delta(1, "one"))
        writer.feed(delta(True, " NOT THIS"))
        writer.feed(delta(1.0, " NOR THIS"))  # type: ignore[arg-type]
        writer.feed(delta(1, ", two"))
        await writer._sync()
        await writer.close_block()
        writer.feed(start(True, text=""))
        writer.feed(delta(True, "NOTHING"))
        writer.feed(delta(1, "OF THIS"))
        await writer._sync()
        return store

    store = asyncio.run(go())
    assert store.written() == {1: "one, two", 2: ""}
    assert "THIS" not in store.everything()
    assert "NOTHING" not in store.everything()


class StuckClear(RecordingStore):
    """A store whose first clear is in flight until it is cancelled, and is then lost."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = asyncio.Event()
        self.lost = 0

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        if not self.lost:
            self.lost += 1
            self.in_flight.set()
            await asyncio.Event().wait()
        await super().clear(run_id, block)


def test_a_pump_cancelled_in_the_middle_of_a_clear_leaves_the_clear_owed() -> None:
    """The run ends while the pump is clearing a block: the pump is cancelled, the clear never
    reached the store. `aclose`, called once the pump has ended, must still clear the buffer.
    (Before issue #80 the state already said "nothing stored", and the buffer stayed.)"""

    async def go() -> StuckClear:
        store = StuckClear()
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0, timeout_s=5)
        pump = asyncio.create_task(writer.pump())
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "an answer"))
        while not store.text:  # noqa: ASYNC110
            await asyncio.sleep(0)
        writer.feed({"type": "message_start"})  # the block is over, an empty one replaces it
        writer._open = False
        writer._wake.set()
        await store.in_flight.wait()  # the pump is clearing, and the store does not answer
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        assert store.meta != {}  # the clear was lost
        await writer.aclose()
        return store

    store = asyncio.run(go())
    assert store.meta == {}
    assert store.text == {}


def test_the_runner_awaits_the_cancelled_pump_before_it_closes_the_live_text(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pumps: list[asyncio.Task[Any]] = []
    seen: list[bool] = []
    pump, aclose = live.LiveText.pump, live.LiveText.aclose

    async def spy_pump(self: live.LiveText) -> None:
        task = asyncio.current_task()
        assert task is not None
        pumps.append(task)
        await pump(self)

    async def spy_aclose(self: live.LiveText) -> None:
        seen.append(pumps[0].done())
        await aclose(self)

    monkeypatch.setattr(live.LiveText, "pump", spy_pump)
    monkeypatch.setattr(live.LiveText, "aclose", spy_aclose)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "hello"})
    assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    assert seen == [True]  # the pump had ended when the buffer was cleared
    assert pumps[0].cancelled()
    assert store.meta == {}


class FailingClear(RecordingStore):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.left = failures
        self.tried = 0

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        self.tried += 1
        if self.left:
            self.left -= 1
            raise ConnectionError("redis hiccup")
        await super().clear(run_id, block)


def writing(store: live.LiveStore, **options: Any) -> live.LiveText:
    writer = live.LiveText(store, RUN, lambda: 3, flush_s=0, close_retry_s=0.01, **options)
    writer.feed(start(0, text=""))
    writer.feed(delta(0, "an answer cut by the end of the run"))
    return writer


def test_a_clear_that_fails_at_the_end_of_the_run_is_tried_again() -> None:
    async def go() -> FailingClear:
        store = FailingClear(failures=1)
        writer = writing(store)
        await writer._sync()
        assert store.meta != {}
        await writer.aclose()
        return store

    store = asyncio.run(go())
    assert store.tried == 2
    assert store.meta == {}
    assert store.text == {}


def test_the_end_of_the_run_clears_the_buffer_even_during_a_back_off() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        writer = writing(store, backoff_s=60)
        await writer._sync()
        store.fail = ConnectionError("down")
        writer.feed(delta(0, ", more"))
        await writer._sync()  # fails: the store is left alone for a minute
        store.fail = None
        await writer.close_block()
        assert store.meta != {}  # the back off holds for a block
        await writer.aclose()  # and not for the end of the run
        return store

    store = asyncio.run(go())
    assert store.meta == {}
    assert store.text == {}


def test_a_clear_that_keeps_failing_is_given_up_after_one_retry_without_raising() -> None:
    async def go() -> tuple[FailingClear, float]:
        store = FailingClear(failures=10)
        writer = writing(store)
        await writer._sync()
        began = time.perf_counter()
        await writer.aclose()
        return store, time.perf_counter() - began

    store, took = asyncio.run(go())
    assert store.tried == 2  # one try, one retry: the buffer then expires by itself (TTL_S)
    assert took < 1.0


def test_closing_a_live_text_that_wrote_nothing_does_not_call_the_store() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0)
        await writer.aclose()
        await writer.aclose()
        return store

    store = asyncio.run(go())
    assert store.writes == 0


def test_a_block_given_up_is_not_asked_of_the_store_again() -> None:
    """Once the clear owed for a broken block is paid, more text of that block calls the store
    for nothing, and closing a block of which nothing is stored returns at once."""

    async def go() -> tuple[RecordingStore, live.LiveText]:
        store = RecordingStore()
        writer = live.LiveText(store, RUN, lambda: 3, flush_s=0, backoff_s=0)
        writer.feed(start(0, text=""))
        writer.feed(delta(0, "one "))
        await writer._sync()
        store.fail = ConnectionError("down")
        writer.feed(delta(0, "two "))
        await writer._sync()
        store.fail = None
        await writer._sync()  # pays the clear
        attempts, writes = writer.attempts, store.writes
        for piece in ("three ", "four"):
            writer.feed(delta(0, piece))
            await writer._sync()
        assert (writer.attempts, store.writes) == (attempts, writes)
        await writer.close_block()
        await writer.close_block()
        assert (writer.attempts, store.writes) == (attempts, writes)
        return store, writer

    store, writer = asyncio.run(go())
    assert store.written() == {1: "one "}
    assert store.meta == {}
    assert writer.failures == 1
