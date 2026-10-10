"""The answer being written, live (issue #33; design and SDK facts in tasks/agents/todo.md).

A run's steps are rows of agent_events, and the text of an answer is one `text` row written when
its content block is complete. This module carries the text *while* it is written, without one
database row per token:

- the worker (`LiveText`, fed by the runner with the SDK's StreamEvent messages) keeps the text of
  the one text block being written and appends it, coalesced every FLUSH_S, to a buffer of that
  block in Redis (`RedisLiveStore`: a small JSON `meta` key naming the block, and one string key
  the text is APPENDed to);
- the api's run stream (`LiveFeed`, one per connection, polled by `app.sse.replay`) reads what it
  has not sent yet from that buffer and sends it as a `partial` event *without an id*:
  {block, after, offset, text}.

Nothing here is durable and nothing has to be. The `text` row stays the record, written exactly
as before, and it replaces the live text in the browser. Resume needs no log of deltas:

- a `partial` carries no SSE id, so Last-Event-ID stays the seq of the last stored step;
- `offset` counts UTF-16 code units (what a JavaScript string is indexed by) from the start of
  the block, and the text of a block only ever grows by appending, so applying a partial is
  idempotent: `text = text[:offset] + chunk`. A new connection always starts a block at offset 0
  with everything written so far. Whatever the moment of a reconnect, the browser holds a prefix
  of the block's text, never a duplicate and never a gap, until the stored `text` step arrives;
- `after` is the seq of the last step stored before the block began: the feed sends a partial
  only once every step up to `after` has gone out on that connection, and the browser drops a
  partial once it holds a `text` step with a greater seq (the block's complete text), which is
  how a delta arriving after the complete message is ignored.

What is never carried: thinking (its deltas are not even read), tool input, and the credential
(the text is scrubbed like every stored event, and a tail that could be the beginning of the
credential is held back until the next characters decide). The text is capped at `cap`
characters per block (the stored event is clipped at the same length), so a very long answer
costs the worker a bounded buffer. Logs carry counts and error types, never text.

When Redis fails, the block's live text is given up (the buffer could be missing a piece) and
the run goes on: the stored step still arrives, as it did before this module existed.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app import sse

logger = logging.getLogger("app.agents.live")

# The worker appends to Redis at most this often per run (seconds).
FLUSH_S = 0.1
# The api reads the buffer this often per open stream (seconds).
POLL_S = 0.15
# One Redis call may take this long before the block's live text is given up.
STORE_TIMEOUT_S = 1.0
# Buffers outlive a run only when its worker died without deleting them: longer than any run
# (wall time 10 minutes plus the stop grace), so a buffer never expires under a running block.
TTL_S = 20 * 60
# After a failed read the feed leaves Redis alone for this long.
READ_BACKOFF_S = 5.0
KEY = "jobapp:agents:live:"


def utf16_units(text: str) -> int:
    """The length of `text` as JavaScript counts it."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


@dataclass(frozen=True)
class Chunk:
    """What a reader gets: the block being written, and its bytes after the reader's offset
    (from the start when the reader was on another block)."""

    block: int
    after: int
    data: bytes
    from_start: bool


class LiveStore(Protocol):
    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        """Start `block` (empty) and forget the `previous` one."""
        ...

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None: ...

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        """No block is being written any more."""
        ...

    async def read(self, run_id: uuid.UUID, block: int | None, offset: int) -> Chunk | None:
        """The block being written and its bytes after `offset` when it is `block`, else all of
        them; None when no block is being written."""
        ...


class MemoryLiveStore:
    """The store in this process: tests, and a worker and an api sharing one event loop."""

    def __init__(self) -> None:
        self.meta: dict[uuid.UUID, tuple[int, int]] = {}
        self.text: dict[tuple[uuid.UUID, int], bytes] = {}
        self.fail: BaseException | None = None  # tests: the next calls raise this
        self.writes = 0

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        self._check()
        self.writes += 1
        if previous is not None:
            self.text.pop((run_id, previous), None)
        self.text[(run_id, block)] = b""
        self.meta[run_id] = (block, after)

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        self._check()
        self.writes += 1
        self.text[(run_id, block)] = self.text.get((run_id, block), b"") + text.encode()

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        self._check()
        self.writes += 1
        self.meta.pop(run_id, None)
        if block is not None:
            self.text.pop((run_id, block), None)

    async def read(self, run_id: uuid.UUID, block: int | None, offset: int) -> Chunk | None:
        self._check()
        found = self.meta.get(run_id)
        if found is None:
            return None
        current, after = found
        same = current == block
        data = self.text.get((run_id, current), b"")
        return Chunk(current, after, data[offset:] if same else data, from_start=not same)


class RedisLiveStore:
    """The store in Redis, shared by the worker (writes) and the api (reads). `pool` returns the
    client: the worker's arq pool, or the api's lazily made one (app.queue.Queue.pool)."""

    def __init__(self, pool: Callable[[], Any]) -> None:
        self._pool = pool

    async def _client(self) -> Any:
        found = self._pool()
        return await found if asyncio.iscoroutine(found) else found

    @staticmethod
    def meta_key(run_id: uuid.UUID) -> str:
        return f"{KEY}{run_id}"

    @staticmethod
    def text_key(run_id: uuid.UUID, block: int) -> str:
        return f"{KEY}{run_id}:{block}"

    async def open(self, run_id: uuid.UUID, block: int, after: int, previous: int | None) -> None:
        client = await self._client()
        async with client.pipeline(transaction=True) as pipe:
            if previous is not None:
                pipe.delete(self.text_key(run_id, previous))
            pipe.delete(self.text_key(run_id, block))
            pipe.set(self.meta_key(run_id), json.dumps({"block": block, "after": after}), ex=TTL_S)
            await pipe.execute()

    async def append(self, run_id: uuid.UUID, block: int, text: str) -> None:
        client = await self._client()
        key = self.text_key(run_id, block)
        async with client.pipeline(transaction=True) as pipe:
            pipe.append(key, text.encode())
            pipe.expire(key, TTL_S)
            pipe.expire(self.meta_key(run_id), TTL_S)
            await pipe.execute()

    async def clear(self, run_id: uuid.UUID, block: int | None) -> None:
        client = await self._client()
        keys = [self.meta_key(run_id)]
        if block is not None:
            keys.append(self.text_key(run_id, block))
        await client.delete(*keys)

    async def read(self, run_id: uuid.UUID, block: int | None, offset: int) -> Chunk | None:
        client = await self._client()
        raw = await client.get(self.meta_key(run_id))
        if raw is None:
            return None
        try:
            meta = json.loads(raw)
            current, after = int(meta["block"]), int(meta["after"])
        except (ValueError, KeyError, TypeError):
            return None
        same = current == block
        data = await client.getrange(self.text_key(run_id, current), offset if same else 0, -1)
        return Chunk(current, after, bytes(data or b""), from_start=not same)


# --- worker side --------------------------------------------------------------------------------


def publishable(text: str, secret: str | None, redacted: str) -> str:
    """`text` as it may leave the worker now: the credential replaced, and a tail that could be
    the beginning of the credential held back until what follows decides. What this returns for
    a text is always the beginning of what it returns once the text has grown."""
    if not secret:
        return text
    safe = text.replace(secret, redacted)
    for size in range(min(len(secret) - 1, len(text)), 0, -1):
        if text.endswith(secret[:size]):
            return safe[: len(safe) - size]
    return safe


class LiveText:
    """The text block a run is writing, published to a LiveStore. One per run.

    `feed(event)` takes the raw API stream event of a StreamEvent and never waits: it only
    updates the buffer. `pump()` (a task of the run) appends what is new to the store, at most
    every `flush_s`. `close_block()` is awaited by the runner after it stored the block's
    complete `text` step; `aclose()` when the run ends.
    """

    def __init__(
        self,
        store: LiveStore,
        run_id: uuid.UUID,
        last_seq: Callable[[], int],
        *,
        secret: str | None = None,
        redacted: str = "[redacted]",
        cap: int = 20_000,
        flush_s: float = FLUSH_S,
        timeout_s: float = STORE_TIMEOUT_S,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._last_seq = last_seq
        self._secret = secret
        self._redacted = redacted
        self._cap = cap
        self._flush_s = flush_s
        self._timeout_s = timeout_s
        # What the run is writing.
        self.block = 0
        self._open = False
        self._index: int | None = None
        self._after = 0
        self._text = ""
        # What the store holds.
        self._stored_block: int | None = None
        self._stored = 0
        self._broken: int | None = None
        self._owed: int | None = None  # a block whose buffer is still to be cleared
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        # Counts for the run's log line (never text).
        self.deltas = 0
        self.dropped = 0
        self.failures = 0

    # The run's stream, in order.

    def feed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "text":
                index = event.get("index")
                self._begin(index if isinstance(index, int) else None)
                first = block.get("text")
                if isinstance(first, str) and first:
                    self._add(first)
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if (
                self._open
                and self._index is not None
                and event.get("index") == self._index
                and isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
            ):
                self.deltas += 1
                self._add(delta["text"])
        elif kind == "message_start" and self._open:
            # A new message while a block is still open: that block was given up (a retried
            # request). An empty block takes its place, so readers drop its text.
            self._begin(None)
        # Everything else (thinking and tool input deltas, stops, pings, future types) is not
        # read at all.

    def _begin(self, index: int | None) -> None:
        self.block += 1
        self._open = True
        self._index = index
        self._after = self._last_seq()
        self._text = ""
        self._wake.set()

    def _add(self, text: str) -> None:
        room = self._cap - len(self._text)
        if room <= 0:
            self.dropped += len(text)
            return
        if len(text) > room:
            self.dropped += len(text) - room
            text = text[:room]
        self._text += text
        self._wake.set()

    # The store.

    async def _sync(self) -> None:
        """Bring the store to what the run is writing now. A failure gives the block's live text
        up (the buffer could miss a piece) and leaves a clear owed, paid by the next call.

        The run goes on while this waits for the store (`feed` never waits), so the block, its
        text and whether it is open are read once, here, and used as read: a block that begins
        meanwhile set the wake flag again and is handled by the next call."""
        async with self._lock:
            block, after, text, is_open = self.block, self._after, self._text, self._open
            touched = self._stored_block if self._stored_block is not None else block
            try:
                async with asyncio.timeout(self._timeout_s):
                    if self._owed is not None:
                        await self._store.clear(self._run_id, self._owed)
                        self._owed = None
                    if not is_open:
                        if self._stored_block is not None:
                            stored, self._stored_block = self._stored_block, None
                            await self._store.clear(self._run_id, stored)
                        return
                    if self._broken == block:
                        return
                    if self._stored_block != block:
                        previous, self._stored_block = self._stored_block, block
                        self._stored = 0
                        await self._store.open(self._run_id, block, after, previous)
                    safe = publishable(text, self._secret, self._redacted)
                    if len(safe) > self._stored:
                        piece, self._stored = safe[self._stored :], len(safe)
                        await self._store.append(self._run_id, block, piece)
            except Exception as exc:
                self.failures += 1
                if is_open:
                    self._broken = block
                self._stored_block = None
                if self._owed is None:
                    self._owed = touched
                if self.failures == 1:
                    logger.warning(
                        "live text not published",
                        extra={"run_id": str(self._run_id), "error": type(exc).__name__},
                    )

    async def pump(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self._sync()
            await asyncio.sleep(self._flush_s)

    async def close_block(self) -> None:
        """The block's complete text is stored (or the block is over): nothing is live now."""
        if not self._open and self._stored_block is None and self._owed is None:
            return
        self._open = False
        self._index = None
        self._text = ""
        await self._sync()

    async def aclose(self) -> None:
        await self.close_block()


# --- api side -----------------------------------------------------------------------------------


class LiveFeed:
    """One connection's view of a run's live text: what `app.sse.replay` polls between its reads
    of the stored steps (module docstring). `stored(kinds)` is told every stored step the
    connection sends, so the feed knows which blocks are already complete."""

    def __init__(
        self,
        store: LiveStore,
        run_id: uuid.UUID,
        *,
        clock: Callable[[], float] = time.monotonic,
        backoff_s: float = READ_BACKOFF_S,
        timeout_s: float = STORE_TIMEOUT_S,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._clock = clock
        self._backoff_s = backoff_s
        self._timeout_s = timeout_s
        self._block: int | None = None
        self._bytes = 0
        self._units = 0
        self._text_seq = 0
        self._present = False
        self._quiet_until = 0.0

    def stored(self, steps: Sequence[tuple[int, str]]) -> None:
        """(seq, kind) of stored steps about to be sent on this connection."""
        for seq, kind in steps:
            if kind == "text":
                self._text_seq = max(self._text_seq, seq)

    async def poll(self, last: int) -> sse.Transient:
        if self._clock() < self._quiet_until:
            return sse.Transient()
        try:
            async with asyncio.timeout(self._timeout_s):
                chunk = await self._store.read(self._run_id, self._block, self._bytes)
        except Exception as exc:
            self._quiet_until = self._clock() + self._backoff_s
            logger.warning(
                "live text not read",
                extra={"run_id": str(self._run_id), "error": type(exc).__name__},
            )
            return sse.Transient()
        if chunk is None:
            # The block that was live is over: its stored step is there, or about to be.
            ended, self._present = self._present, False
            return sse.Transient(read_now=ended)
        self._present = True
        if chunk.after < self._text_seq:
            return sse.Transient()  # its complete text already went out
        if chunk.after > last:
            return sse.Transient(read_now=True)  # the steps before the block go first
        try:
            text = chunk.data.decode()
        except UnicodeDecodeError:  # pragma: no cover, appends are whole characters
            return sse.Transient()
        if chunk.from_start:
            self._block, self._bytes, self._units = chunk.block, 0, 0
        elif not text:
            return sse.Transient()
        data = {"block": chunk.block, "after": chunk.after, "offset": self._units, "text": text}
        self._bytes += len(chunk.data)
        self._units += utf16_units(text)
        return sse.Transient(events=(sse.format_transient("partial", data),))
