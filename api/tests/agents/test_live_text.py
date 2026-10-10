"""app/agents/live.py without a database (issue #33): what the worker publishes of a text block
(`LiveText`), what one connection relays of it (`LiveFeed` under `app.sse.replay`), and the
resume guarantee: whatever the moment a client is cut and reconnects with its Last-Event-ID, it
ends with every step once and the full text once, and what it shows meanwhile is always a
beginning of the text being written."""

import asyncio
import logging
import random
import uuid
from typing import Any

import pytest

from app import sse
from app.agents import live
from tests.agents.live_helpers import Browser, RecordingStore, parse

RUN = uuid.uuid4()
SECRET = "sk-ant-api03-" + "Ab1_" * 20 + "WXyz"
# Accents, a combining mark, CJK, an emoji outside the BMP and a joined emoji sequence.
UNICODE = "café déjà vu, été, 日本語, \U0001f600 and \U0001f469‍\U0001f4bb done"


def start(index: int, kind: str = "text", **block: Any) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": {"type": kind, **block}}


def delta(index: int, text: str, kind: str = "text_delta", key: str = "text") -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": {"type": kind, key: text}}


def make(store: live.LiveStore, seq: list[int] | None = None, **options: Any) -> live.LiveText:
    counter = seq if seq is not None else [3]
    # No back off after a failed write unless a test asks for one (issue #80; the back off has
    # its own tests in test_live_hardening.py).
    options.setdefault("backoff_s", 0)
    return live.LiveText(store, RUN, lambda: counter[0], flush_s=0, **options)


# --- what may leave the worker ------------------------------------------------------------------


def test_publishable_scrubs_the_secret_and_holds_back_its_beginning() -> None:
    assert live.publishable("plain text", None, "[x]") == "plain text"
    assert live.publishable("plain text", SECRET, "[x]") == "plain text"
    assert live.publishable(f"key {SECRET} end", SECRET, "[x]") == "key [x] end"
    # A tail that is the beginning of the secret waits for what follows.
    assert live.publishable("key " + SECRET[:9], SECRET, "[x]") == "key "
    assert live.publishable("key s", SECRET, "[x]") == "key "
    # Complete: replaced at once. A second, partial one after it is held back.
    assert live.publishable(f"{SECRET}{SECRET[:4]}", SECRET, "[x]") == "[x]"


def test_publishable_only_ever_grows_whatever_the_cut() -> None:
    text = f"my key is {SECRET}, again {SECRET[:20]} no, and {SECRET}."
    whole = text.replace(SECRET, "[redacted]")
    previous = ""
    for size in range(len(text) + 1):
        shown = live.publishable(text[:size], SECRET, "[redacted]")
        assert shown.startswith(previous), size
        assert whole.startswith(shown), size
        assert SECRET not in shown
        previous = shown
    assert previous == whole


def test_utf16_units_count_like_javascript() -> None:
    assert live.utf16_units("") == 0
    assert live.utf16_units("abc") == 3
    assert live.utf16_units("é日") == 2
    assert live.utf16_units("\U0001f600") == 2  # one code point, two units
    assert live.utf16_units("\ud83d") == 1  # a lone surrogate does not fail


# --- LiveText: the worker side ------------------------------------------------------------------


def test_only_text_deltas_of_the_open_text_block_are_published() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        text = make(store)
        text.feed({"type": "message_start", "message": {"id": "m"}})
        text.feed(start(0, "thinking", thinking=""))
        text.feed(delta(0, "SECRET-THOUGHT", "thinking_delta", "thinking"))
        text.feed(delta(0, "c2ln", "signature_delta", "signature"))
        await text._sync()
        assert store.ops == []  # nothing is live during thinking
        text.feed(start(1, text=""))
        text.feed({"type": "ping"})
        text.feed(delta(1, "Hel"))
        text.feed(delta(0, "late thinking", "thinking_delta", "thinking"))
        text.feed(delta(2, "another block"))
        text.feed(delta(1, "lo"))
        await text._sync()
        text.feed(start(2, "tool_use", id="t", name="x", input={}))
        text.feed(delta(2, '{"a": 1}', "input_json_delta", "partial_json"))
        text.feed({"type": "content_block_stop", "index": 1})
        text.feed({"type": "a_future_event", "index": 1})
        text.feed("not a dict")
        text.feed({"type": "content_block_delta", "index": 1, "delta": "broken"})
        text.feed({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta"}})
        await text._sync()
        return store

    store = asyncio.run(go())
    assert store.ops == [("open", 1, 3), ("append", 1, "Hello")]
    assert "THOUGHT" not in store.everything()
    assert "another block" not in store.everything()
    assert '{"a"' not in store.everything()


def test_deltas_are_coalesced_by_the_pump(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deltas that arrive while the pump pauses between two writes go out in one write. The
    pause is held by the test, not by a clock: real time decides nothing here."""
    pieces = ("two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    turn = asyncio.sleep  # the real one, to let the pump run
    pauses: list[float] = []

    async def go() -> tuple[RecordingStore, live.LiveText, int]:
        store = RecordingStore()
        resume = asyncio.Event()

        async def pause(seconds: float) -> None:
            pauses.append(seconds)
            await resume.wait()
            resume.clear()

        def appends() -> int:
            return len([op for op in store.ops if op[0] == "append"])

        monkeypatch.setattr(live.asyncio, "sleep", pause)
        text = live.LiveText(store, RUN, lambda: 3, flush_s=0.05)
        pump = asyncio.create_task(text.pump())
        text.feed(start(0, text=""))
        text.feed(delta(0, "one "))
        while len(pauses) < 1:  # the first delta is written at once, then the pump pauses
            await turn(0)
        assert appends() == 1
        for piece in pieces:
            text.feed(delta(0, piece + " "))
            await turn(0)  # the pump gets its turn after every delta, and stays paused
        during = appends()
        resume.set()
        while len(pauses) < 2:
            await turn(0)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        return store, text, during

    store, text, during = asyncio.run(go())
    assert during == 1  # nothing was written while the pump paused
    assert text.deltas == 10
    assert [op for op in store.ops if op[0] == "append"] == [
        ("append", 1, "one "),
        ("append", 1, "two three four five six seven eight nine ten "),
    ]
    assert pauses == [0.05, 0.05]  # each write is followed by a pause of flush_s


def test_complete_text_clears_the_block_and_the_next_one_starts_fresh() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        seq = [3]
        text = make(store, seq)
        text.feed(start(0, text=""))
        text.feed(delta(0, "first"))
        await text._sync()
        seq[0] = 4  # the runner stored the block's text step
        await text.close_block()
        assert await store.read(RUN, None, 0) is None
        text.feed(delta(0, " late delta after the complete message"))
        await text._sync()
        seq[0] = 6
        text.feed(start(0, text="Se"))  # a start may carry text already
        text.feed(delta(0, "cond"))
        await text._sync()
        await text.aclose()
        await text.aclose()  # twice is fine
        return store

    store = asyncio.run(go())
    assert store.ops == [
        ("open", 1, 3),
        ("append", 1, "first"),
        ("clear", 1),
        ("open", 2, 6),
        ("append", 2, "Second"),
        ("clear", 2),
    ]
    assert store.meta == {}
    assert store.text == {}


def test_a_block_opened_and_closed_before_any_flush_writes_nothing() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        text = make(store)
        text.feed(start(0, text=""))
        text.feed(delta(0, "quick"))
        await text.close_block()
        return store

    assert asyncio.run(go()).ops == []


def test_a_new_message_while_a_block_is_open_replaces_it_with_an_empty_block() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        text = make(store)
        text.feed({"type": "message_start"})  # nothing open: nothing happens
        text.feed(start(0, text=""))
        text.feed(delta(0, "abandoned"))
        await text._sync()
        text.feed({"type": "message_start"})  # the request was retried
        text.feed(delta(0, "ignored: no text block is open in the new message"))
        await text._sync()
        chunk = await store.read(RUN, 1, 9)
        assert chunk is not None
        assert (chunk.block, chunk.data, chunk.from_start) == (2, b"", True)
        return store

    assert asyncio.run(go()).ops == [("open", 1, 3), ("append", 1, "abandoned"), ("open", 2, 3)]


def test_a_very_long_block_is_capped_in_memory_and_in_the_store() -> None:
    async def go() -> tuple[RecordingStore, live.LiveText]:
        store = RecordingStore()
        text = make(store, cap=1000)
        text.feed(start(0, text=""))
        for _ in range(500):
            text.feed(delta(0, "x" * 37))
        await text._sync()
        text.feed(delta(0, "more"))
        await text._sync()
        return store, text

    store, text = asyncio.run(go())
    assert store.written() == {1: "x" * 1000}
    assert len(text._text) == 1000  # the worker holds the cap, not the answer
    assert text.dropped == 500 * 37 - 1000 + 4


def test_the_secret_never_reaches_the_store_even_split_across_deltas() -> None:
    async def go() -> RecordingStore:
        store = RecordingStore()
        text = make(store, secret=SECRET)
        text.feed(start(0, text=""))
        answer = f"Your key is {SECRET}, keep it safe."
        for i in range(0, len(answer), 5):
            text.feed(delta(0, answer[i : i + 5]))
            await text._sync()  # a flush after every delta: the worst case
        return store

    store = asyncio.run(go())
    assert store.written() == {1: "Your key is [redacted], keep it safe."}
    for op in store.ops:
        if op[0] == "append":
            assert "sk-ant" not in op[2]


def test_a_failing_store_gives_the_block_up_and_the_next_block_tries_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def go() -> tuple[RecordingStore, live.LiveText]:
        store = RecordingStore()
        text = make(store)
        text.feed(start(0, text=""))
        text.feed(delta(0, "one "))
        await text._sync()
        store.fail = ConnectionError("redis is down: SENSITIVE")
        text.feed(delta(0, "two "))
        await text._sync()
        store.fail = None
        text.feed(delta(0, "three"))
        await text._sync()  # given up: a buffer missing "two " must not be continued
        assert await store.read(RUN, None, 0) is None  # and the broken buffer is cleared
        await text.close_block()
        text.feed(start(0, text=""))
        text.feed(delta(0, "next"))
        await text._sync()
        return store, text

    with caplog.at_level(logging.WARNING):
        store, text = asyncio.run(go())
    assert store.written() == {1: "one ", 2: "next"}
    assert text.failures == 1
    assert "live text not published" in caplog.text
    assert "SENSITIVE" not in caplog.text
    assert "one" not in caplog.text


def test_a_block_that_begins_while_the_store_is_written_is_published_whole() -> None:
    """The run does not wait for the store: while one write is in flight the block can end and
    the next one begin. Nothing of one block may land in the other's buffer."""

    class Slow(RecordingStore):
        def __init__(self) -> None:
            super().__init__()
            self.gate = asyncio.Event()
            self.held = 0

        async def open(
            self, run_id: uuid.UUID, block: int, after: int, previous: int | None
        ) -> None:
            if block == 1:
                self.held += 1
                await self.gate.wait()  # the write of block 1 is in flight
            await super().open(run_id, block, after, previous)

    async def go() -> Slow:
        store = Slow()
        seq = [3]
        text = live.LiveText(store, RUN, lambda: seq[0], flush_s=0, timeout_s=5)
        pump = asyncio.create_task(text.pump())
        text.feed(start(0, text=""))
        text.feed(delta(0, "first block"))
        while not store.held:  # noqa: ASYNC110
            await asyncio.sleep(0)
        # Meanwhile the request is retried: a new message, a new text block.
        seq[0] = 5
        text.feed({"type": "message_start"})
        text.feed(start(0, text=""))
        text.feed(delta(0, "second block"))
        store.gate.set()
        for _ in range(50):
            await asyncio.sleep(0.001)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        return store

    store = asyncio.run(go())
    written = store.written()
    assert written[1] == "first block"  # what block 1 held when its write began, no more
    assert written[text_block := max(written)] == "second block"
    assert store.meta[RUN] == (text_block, 5)
    assert store.text == {(RUN, text_block): b"second block"}


def test_a_slow_store_times_out_instead_of_holding_the_run() -> None:
    class Stuck(RecordingStore):
        async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
            await asyncio.sleep(30)

    async def go() -> live.LiveText:
        text = live.LiveText(Stuck(), RUN, lambda: 3, flush_s=0, timeout_s=0.05)
        text.feed(start(0, text=""))
        text.feed(delta(0, "x"))
        await asyncio.wait_for(text._sync(), timeout=2)
        await asyncio.wait_for(text.close_block(), timeout=2)
        return text

    assert asyncio.run(go()).failures == 1


# --- LiveFeed: one connection -------------------------------------------------------------------


def poll(feed: live.LiveFeed, last: int) -> tuple[list[dict[str, Any]], bool]:
    found = asyncio.run(feed.poll(last))
    return [data for _, _, data in parse(list(found.events))], found.read_now


def test_feed_sends_what_is_new_with_utf16_offsets() -> None:
    store = RecordingStore()
    feed = live.LiveFeed(store, RUN)
    assert poll(feed, 3) == ([], False)
    asyncio.run(store.open(RUN, 1, 3, None))
    assert poll(feed, 3) == ([{"block": 1, "after": 3, "offset": 0, "text": ""}], False)
    assert poll(feed, 3) == ([], False)  # nothing new: nothing sent
    asyncio.run(store.append(RUN, 1, "é\U0001f600"))
    assert poll(feed, 3) == ([{"block": 1, "after": 3, "offset": 0, "text": "é\U0001f600"}], False)
    asyncio.run(store.append(RUN, 1, "日本"))
    assert poll(feed, 3) == ([{"block": 1, "after": 3, "offset": 3, "text": "日本"}], False)
    # A second connection starts the block from its beginning.
    other = live.LiveFeed(store, RUN)
    assert poll(other, 3) == (
        [{"block": 1, "after": 3, "offset": 0, "text": "é\U0001f600日本"}],
        False,
    )
    assert b"id:" not in asyncio.run(live.LiveFeed(store, RUN).poll(3)).events[0]


def test_feed_waits_for_the_steps_before_the_block_and_drops_a_stale_block() -> None:
    store = RecordingStore()
    feed = live.LiveFeed(store, RUN)
    asyncio.run(store.open(RUN, 1, 5, None))
    asyncio.run(store.append(RUN, 1, "text"))
    # The connection has sent the steps up to 3 only: the log is read first.
    assert poll(feed, 3) == ([], True)
    assert poll(feed, 5) == ([{"block": 1, "after": 5, "offset": 0, "text": "text"}], False)
    # The block's complete text (step 6) went out before the worker cleared the buffer.
    feed.stored([(6, "text")])
    asyncio.run(store.append(RUN, 1, " a delta after the complete message"))
    assert poll(feed, 6) == ([], False)
    asyncio.run(store.clear(RUN, 1))
    assert poll(feed, 6) == ([], True)  # it just ended: read the log now
    assert poll(feed, 6) == ([], False)
    # Steps that are not text do not make a block stale.
    asyncio.run(store.open(RUN, 2, 6, None))
    feed.stored([(7, "rate_limit")])
    assert poll(feed, 7) == ([{"block": 2, "after": 6, "offset": 0, "text": ""}], False)


def test_feed_survives_a_failing_store_and_backs_off(caplog: pytest.LogCaptureFixture) -> None:
    store = RecordingStore()
    now = [100.0]
    feed = live.LiveFeed(store, RUN, clock=lambda: now[0], backoff_s=5)
    asyncio.run(store.open(RUN, 1, 0, None))
    asyncio.run(store.append(RUN, 1, "abc"))
    store.fail = ConnectionError("down")
    with caplog.at_level(logging.WARNING):
        assert poll(feed, 0) == ([], False)
    assert "live text not read" in caplog.text
    store.fail = None
    assert poll(feed, 0) == ([], False)  # left alone for a while
    now[0] += 5.1
    assert poll(feed, 0) == ([{"block": 1, "after": 0, "offset": 0, "text": "abc"}], False)


# --- replay with a transient source -------------------------------------------------------------


def test_replay_sends_transient_events_between_reads_and_never_after_the_end() -> None:
    reads: list[int] = []
    polls: list[int] = []

    def read(after: int) -> sse.Page:
        reads.append(after)
        if len(reads) < 4:
            return sse.Page([], final=False)
        events = [sse.Event(1, "step", {"n": 1})] if after == 0 else []
        return sse.Page(events, final=True)

    async def transient(last: int) -> sse.Transient:
        polls.append(last)
        return sse.Transient(events=(sse.format_transient("partial", {"n": len(polls)}),))

    async def go() -> list[bytes]:
        return [
            chunk
            async for chunk in sse.replay(
                read, interval_s=0, transient=transient, transient_interval_s=0
            )
        ]

    found = parse(asyncio.run(go()))
    names = [name for name, _, _ in found]
    assert names == ["partial", "partial", "partial", "step", "end"]
    assert all(event_id is None for name, event_id, _ in found if name == "partial")
    assert polls == [0, 0, 0]  # not polled once the log is final


def test_replay_reads_the_log_at_once_when_the_transient_source_asks() -> None:
    reads: list[int] = []

    def read(after: int) -> sse.Page:
        reads.append(after)
        return sse.Page([], final=len(reads) >= 4)

    async def transient(last: int) -> sse.Transient:
        return sse.Transient(read_now=True)

    async def go() -> list[bytes]:
        # The log would be read every 30 s: only `read_now` explains four reads in a moment.
        return [
            chunk
            async for chunk in sse.replay(
                read, interval_s=30, transient=transient, transient_interval_s=0.01
            )
        ]

    assert asyncio.run(asyncio.wait_for(go(), timeout=5))[-1] == sse.END
    assert len(reads) == 4


# --- the resume guarantee -----------------------------------------------------------------------


def at_once(coroutine: Any) -> None:
    """Run a coroutine that never waits (the memory store's) without an event loop."""
    with pytest.raises(StopIteration):
        coroutine.send(None)


class World:
    """A run as the api sees it, one change at a time: steps appear in the log, the worker opens
    a block, appends to it, clears it. `advance` applies the next change; the stream under test
    is made to advance the world as it polls, so every interleaving of "the client reads" and
    "the run moves on" is reached by cutting the client at every event."""

    def __init__(self, changes: list[tuple[Any, ...]]) -> None:
        self.changes = list(changes)
        self.position = 0
        self.steps: list[tuple[int, str, dict[str, Any]]] = []
        self.store = RecordingStore()
        self.final = False
        self.truth: dict[int, str] = {}

    def feed(self) -> live.LiveFeed:
        """The feed of one connection."""
        return live.LiveFeed(self.store, RUN)

    def advance(self) -> None:
        if self.position >= len(self.changes):
            return
        change = self.changes[self.position]
        self.position += 1
        kind = change[0]
        if kind == "step":
            _, step_kind, data = change
            self.steps.append((len(self.steps) + 1, step_kind, data))
            if step_kind == "status" and data.get("status") in ("done", "failed", "cancelled"):
                self.final = True
        elif kind == "open":
            at_once(self.store.open(RUN, change[1], len(self.steps), None))
            self.truth[change[1]] = ""
        elif kind == "append":
            at_once(self.store.append(RUN, change[1], change[2]))
            self.truth[change[1]] += change[2]
        elif kind == "clear":
            at_once(self.store.clear(RUN, change[1]))

    def page(self, after: int, feed: live.LiveFeed) -> sse.Page:
        rows = [row for row in self.steps if row[0] > after][:3]
        feed.stored([(seq, kind) for seq, kind, _ in rows])
        events = [
            sse.Event(seq, "step", {"seq": seq, "at": "t", "kind": kind, "data": data})
            for seq, kind, data in rows
        ]
        return sse.Page(events, final=self.final and len(rows) < 3)


def pieces_of(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def answer_world(
    first: str, second: str, size: int, *, clear_first: bool, ending: str
) -> list[tuple[Any, ...]]:
    """A run answering in two text blocks with tool steps in between. `clear_first`: the worker's
    buffer is cleared before the block's text step is visible (else after: the stale window)."""

    def block(number: int, text: str, complete: bool = True) -> list[tuple[Any, ...]]:
        changes: list[tuple[Any, ...]] = [("open", number)]
        changes += [("append", number, piece) for piece in pieces_of(text, size)]
        if not complete:
            return changes
        stored = ("step", "text", {"text": text})
        cleared = ("clear", number)
        return changes + ([cleared, stored] if clear_first else [stored, cleared])

    changes: list[tuple[Any, ...]] = [
        ("step", "status", {"status": "queued"}),
        ("step", "status", {"status": "running"}),
        ("step", "init", {"model": "m"}),
        ("step", "thinking", {}),
        *block(1, first),
        ("step", "tool_call", {"name": "get_job"}),
        ("step", "rate_limit", {"status": "allowed"}),
        ("step", "tool_result", {"content": "ok"}),
    ]
    if ending == "done":
        changes += [
            *block(2, second),
            ("step", "usage", {"input_tokens": 1}),
            ("step", "status", {"status": "done"}),
        ]
    else:
        # Cancelled or failed in the middle of the second block: no text step for it.
        changes += [*block(2, second, complete=False), ("step", "status", {"status": ending})]
        changes += [("clear", 2)]
    return changes


def connect(world: World, browser: Browser, stop_after: int | None, budget: list[int]) -> bool:
    """One connection of `browser`, resumed from its Last-Event-ID, cut after `stop_after` events
    (None: until the stream ends). The world advances one change per poll. Returns whether the
    stream reached its end."""
    feed = world.feed()

    async def transient(last: int) -> sse.Transient:
        found = await feed.poll(last)
        world.advance()
        return found

    async def go() -> bool:
        taken = 0
        stream = sse.replay(
            lambda after: world.page(after, feed),
            after=browser.last_id,
            interval_s=0,
            transient=transient,
            transient_interval_s=0,
        )
        try:
            async for chunk in stream:
                for event in parse([chunk]):
                    if event[0] == "end":
                        return True
                    browser.take(event)
                    check(world, browser)
                    taken += 1
                    budget[0] -= 1
                    assert budget[0] > 0, "the stream does not converge"
                    if stop_after is not None and taken >= stop_after:
                        return False
        finally:
            await stream.aclose()
        return False

    return asyncio.run(go())


def check(world: World, browser: Browser) -> None:
    """What holds at every moment: steps once and in order, and the live text is a beginning of
    what the worker has written of that block."""
    seqs = [step["seq"] for step in browser.steps]
    assert seqs == list(range(1, len(seqs) + 1))
    shown = browser.live_text()
    if shown is not None and browser.live is not None:
        assert world.truth[browser.live["block"]].startswith(shown)


def finish(world: World, browser: Browser, cuts: list[int], gap: int) -> None:
    budget = [5000]
    for cut in cuts:
        if connect(world, browser, cut, budget):
            break
        for _ in range(gap):  # the run moves on while the client is away
            world.advance()
    else:
        assert connect(world, browser, None, budget)


@pytest.mark.parametrize("clear_first", [True, False])
@pytest.mark.parametrize("gap", [0, 2, 50])
def test_reconnect_at_every_event_ends_with_the_full_text_once(clear_first: bool, gap: int) -> None:
    first, second = "Let me read: " + UNICODE, UNICODE * 3
    changes = answer_world(first, second, 7, clear_first=clear_first, ending="done")
    # How many events an uncut client sees: the cut points to try.
    whole = Browser()
    finish(World(changes), whole, [], 0)
    total = len(whole.steps) + len(whole.shown)
    assert whole.texts() == [first, second]
    assert len(whole.shown) > 10, "the answer must be seen partial before it is complete"
    assert any(text != second and second.startswith(text) for _, text in whole.shown)
    for cut in range(1, total + 1):
        browser = Browser()
        world = World(changes)
        finish(world, browser, [cut], gap)
        assert [step["seq"] for step in browser.steps] == list(range(1, len(world.steps) + 1)), cut
        assert browser.texts() == [first, second], cut
        assert browser.live is None, cut
        assert browser.ended, cut
        for block, text in browser.shown:
            assert world.truth[block].startswith(text), cut


@pytest.mark.parametrize("seed", range(40))
def test_reconnecting_many_times_at_random_moments(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311, a test's dice
    first = "".join(rng.choice("ab é日\U0001f600́") for _ in range(rng.randint(0, 40)))
    second = "".join(rng.choice("xyz \n&<>é日\U0001f469‍") for _ in range(rng.randint(1, 300)))
    changes = answer_world(
        first or "x",
        second,
        rng.randint(1, 9),
        clear_first=rng.random() < 0.5,
        ending="done",
    )
    world, browser = World(changes), Browser()
    cuts = [rng.randint(1, 6) for _ in range(rng.randint(1, 60))]
    finish(world, browser, cuts, rng.choice([0, 1, 3]))
    assert [step["seq"] for step in browser.steps] == list(range(1, len(world.steps) + 1))
    assert browser.texts() == [first or "x", second]
    assert browser.live is None


@pytest.mark.parametrize("ending", ["cancelled", "failed"])
def test_a_run_stopped_mid_answer_leaves_no_live_text_whatever_the_cut(ending: str) -> None:
    changes = answer_world("first", UNICODE * 2, 5, clear_first=True, ending=ending)
    whole = Browser()
    finish(World(changes), whole, [], 0)
    assert any(block == 2 and text for block, text in whole.shown)  # it was being shown
    for cut in range(1, len(whole.steps) + len(whole.shown) + 1):
        browser = Browser()
        finish(World(changes), browser, [cut], 1)
        assert browser.texts() == ["first"], cut  # the cut block is stored nowhere
        assert browser.live is None, cut
        assert browser.ended, cut


def test_two_tabs_and_a_slow_client_see_the_same_answer() -> None:
    """Two connections on one run, one of them reading only now and then: each has its own
    offsets, and the slow one gets what it missed in one piece, not a queue of deltas."""
    store = RecordingStore()
    fast, slow = live.LiveFeed(store, RUN), live.LiveFeed(store, RUN)
    tabs = {"fast": Browser(), "slow": Browser()}
    text = UNICODE * 20
    asyncio.run(store.open(RUN, 1, 0, None))
    slow_events = 0
    for number, piece in enumerate(pieces_of(text, 3)):
        asyncio.run(store.append(RUN, 1, piece))
        for event in parse(list(asyncio.run(fast.poll(0)).events)):
            tabs["fast"].take(event)
        if number % 40 == 0:
            for event in parse(list(asyncio.run(slow.poll(0)).events)):
                tabs["slow"].take(event)
                slow_events += 1
    for event in parse(list(asyncio.run(slow.poll(0)).events)):
        tabs["slow"].take(event)
        slow_events += 1
    assert tabs["fast"].live_text() == text
    assert tabs["slow"].live_text() == text
    assert slow_events < len(pieces_of(text, 3)) / 20


# --- the same guarantee with a feed that reads only while the run is running (issue #80) --------


class GatedWorld(World):
    """The world as the run stream sees it since issue #80: a feed that starts idle and is told
    at every read of the log whether the run is running, and reads the store only then."""

    def __init__(self, changes: list[tuple[Any, ...]]) -> None:
        super().__init__(changes)
        self.feeds: list[live.LiveFeed] = []

    def feed(self) -> live.LiveFeed:
        made = live.LiveFeed(self.store, RUN, running=False)
        self.feeds.append(made)
        return made

    def running(self) -> bool:
        statuses = [data.get("status") for _, kind, data in self.steps if kind == "status"]
        return bool(statuses) and statuses[-1] == "running"

    def page(self, after: int, feed: live.LiveFeed) -> sse.Page:
        feed.active(self.running())
        return super().page(after, feed)


@pytest.mark.parametrize("clear_first", [True, False])
@pytest.mark.parametrize("gap", [0, 2, 50])
def test_gated_reconnect_at_every_event_ends_with_the_full_text_once(
    clear_first: bool, gap: int
) -> None:
    first, second = "Let me read: " + UNICODE, UNICODE * 3
    changes = answer_world(first, second, 7, clear_first=clear_first, ending="done")
    whole = Browser()
    finish(GatedWorld(changes), whole, [], 0)
    total = len(whole.steps) + len(whole.shown)
    assert whole.texts() == [first, second]
    assert len(whole.shown) > 10, "the answer must be seen partial before it is complete"
    assert any(text != second and second.startswith(text) for _, text in whole.shown)
    for cut in range(1, total + 1):
        browser = Browser()
        world = GatedWorld(changes)
        finish(world, browser, [cut], gap)
        assert [step["seq"] for step in browser.steps] == list(range(1, len(world.steps) + 1)), cut
        assert browser.texts() == [first, second], cut
        assert browser.live is None, cut
        assert browser.ended, cut
        for block, text in browser.shown:
            assert world.truth[block].startswith(text), cut


@pytest.mark.parametrize("seed", range(40))
def test_gated_reconnecting_many_times_at_random_moments(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311, a test's dice
    first = "".join(rng.choice("ab é日\U0001f600\u0301") for _ in range(rng.randint(0, 40)))
    second = "".join(rng.choice("xyz \n&<>é日\U0001f469\u200d") for _ in range(rng.randint(1, 300)))
    changes = answer_world(
        first or "x",
        second,
        rng.randint(1, 9),
        clear_first=rng.random() < 0.5,
        ending="done",
    )
    world, browser = GatedWorld(changes), Browser()
    cuts = [rng.randint(1, 6) for _ in range(rng.randint(1, 60))]
    finish(world, browser, cuts, rng.choice([0, 1, 3]))
    assert [step["seq"] for step in browser.steps] == list(range(1, len(world.steps) + 1))
    assert browser.texts() == [first or "x", second]
    assert browser.live is None


@pytest.mark.parametrize("ending", ["cancelled", "failed"])
def test_gated_a_run_stopped_mid_answer_leaves_no_live_text_whatever_the_cut(ending: str) -> None:
    changes = answer_world("first", UNICODE * 2, 5, clear_first=True, ending=ending)
    whole = Browser()
    finish(GatedWorld(changes), whole, [], 0)
    assert any(block == 2 and text for block, text in whole.shown)  # it was being shown
    for cut in range(1, len(whole.steps) + len(whole.shown) + 1):
        browser, world = Browser(), GatedWorld(changes)
        finish(world, browser, [cut], 1)
        assert browser.texts() == ["first"], cut  # the cut block is stored nowhere
        assert browser.live is None, cut
        assert browser.ended, cut
        assert not world.feeds[-1]._running, cut  # the last feed was told the run had ended


def test_a_feed_reads_the_store_only_while_the_run_is_running() -> None:
    store = RecordingStore()
    feed = live.LiveFeed(store, RUN, running=False)
    asyncio.run(store.open(RUN, 1, 0, None))
    asyncio.run(store.append(RUN, 1, "stale"))
    for _ in range(50):  # queued: a buffer left by an earlier life of the key is never read
        assert poll(feed, 0) == ([], False)
    assert feed.reads == 0
    feed.active(True)
    assert poll(feed, 0) == ([{"block": 1, "after": 0, "offset": 0, "text": "stale"}], False)
    assert poll(feed, 0) == ([], False)
    assert feed.reads == 2  # running: every poll reads, block or no block
    feed.active(False)  # ended
    asyncio.run(store.append(RUN, 1, " more"))
    for _ in range(50):
        assert poll(feed, 0) == ([], False)
    assert feed.reads == 2
    # Running again (a status read in between was stale): it continues where it was.
    feed.active(True)
    assert poll(feed, 0) == ([{"block": 1, "after": 0, "offset": 5, "text": " more"}], False)


def test_a_feed_made_without_options_reads_at_every_poll_as_before() -> None:
    store = RecordingStore()
    feed = live.LiveFeed(store, RUN)
    for _ in range(5):
        assert poll(feed, 0) == ([], False)
    assert feed.reads == 5
