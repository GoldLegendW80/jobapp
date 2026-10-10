"""Server-sent events: the helpers of every live view (sweep progress, agent runs).

`watch(read, ...)` turns a function that reads the current state of something into an event
stream: it reads every `interval_s`, sends an event each time the state changed (the first read
always), a comment line every `heartbeat_s` so proxies keep the connection open, and ends after
the state says it is final or after `max_s`. Each event carries an increasing `id`; a client
that reconnects (EventSource does so by itself, after `retry_ms`) gets the current state again
first, so nothing is lost by resuming from scratch.

`replay(read, after=...)` streams a log that only grows (agent_events of a run): each stored
event once, with its own id, from after the client's Last-Event-ID (`last_event_id`), then the
new ones as they are written, and an `end` event once the log is final. Between its reads of
the log it can poll a `transient` source (agents: the answer being written, app/agents/live.py)
whose events carry no id: they never move Last-Event-ID, so a reconnect still resumes the log
exactly where it stopped, and the source itself says from where it continues.

`response(events)` wraps a stream in a StreamingResponse with the headers live streams need:
text/event-stream, no caching, no transformation (Caddy compresses text/* otherwise, which would
hold events back) and no proxy buffering.

Reads go through `asyncio.to_thread`, so a synchronous SQLAlchemy read never blocks the loop.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi.responses import StreamingResponse

HEADERS = {
    # no-transform: Caddy's encode directive leaves the stream uncompressed (and so unbuffered).
    "Cache-Control": "no-store, no-transform",
    "X-Accel-Buffering": "no",
}


@dataclass(frozen=True)
class Snapshot:
    event: str  # the event name, e.g. "progress" or "done"
    data: Any  # JSON-serialisable
    final: bool = False


def format_event(event_id: int, event: str, data: Any) -> bytes:
    body = json.dumps(data, separators=(",", ":"), default=str)
    return f"id: {event_id}\nevent: {event}\ndata: {body}\n\n".encode()


async def watch(
    read: Callable[[], Snapshot | None],
    *,
    interval_s: float = 0.5,
    heartbeat_s: float = 15.0,
    max_s: float = 30 * 60,
    retry_ms: int = 2000,
) -> AsyncIterator[bytes]:
    """The event stream of `read` (module docstring). `read` returning None ends the stream with
    a `gone` event (the thing was deleted meanwhile)."""
    yield f"retry: {retry_ms}\n\n".encode()
    started = last_sent = time.monotonic()
    previous: Any = object()
    event_id = 0
    while True:
        snapshot = await asyncio.to_thread(read)
        if snapshot is None:
            event_id += 1
            yield format_event(event_id, "gone", {})
            return
        if snapshot.data != previous or snapshot.final:
            event_id += 1
            previous = snapshot.data
            last_sent = time.monotonic()
            yield format_event(event_id, snapshot.event, snapshot.data)
            if snapshot.final:
                return
        now = time.monotonic()
        if now - started >= max_s:
            return
        if now - last_sent >= heartbeat_s:
            last_sent = now
            yield b": ping\n\n"
        await asyncio.sleep(interval_s)


@dataclass(frozen=True)
class Event:
    id: int  # increasing; what a reconnecting client sends back as Last-Event-ID
    event: str
    data: Any  # JSON-serialisable


@dataclass(frozen=True)
class Page:
    events: Sequence[Event]
    # True when nothing will ever follow these events (the thing has ended).
    final: bool


@dataclass(frozen=True)
class Transient:
    """What a transient source has now: events already formatted (`format_transient`), and
    whether the log should be read at once (the source knows something was just written)."""

    events: Sequence[bytes] = ()
    read_now: bool = False


def format_transient(event: str, data: Any) -> bytes:
    """An event without an id: the browser's Last-Event-ID stays what it was."""
    body = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


END = b"event: end\ndata: {}\n\n"


def last_event_id(header: str | None, query: str | None = None) -> int:
    """Where a stream resumes: the Last-Event-ID header (sent by EventSource when it reconnects
    by itself), else the `after` query parameter (a page opening the stream with what it already
    has), else 0, from the start. Anything that is not a non-negative integer counts as 0: the
    client gets everything again, never a gap."""
    for raw in (header, query):
        if raw is None or not raw.strip():
            continue
        text = raw.strip()
        if text.isdigit() and len(text) <= 18:
            return int(text)
        return 0
    return 0


async def replay(
    read: Callable[[int], Page | None],
    *,
    after: int = 0,
    interval_s: float = 0.5,
    heartbeat_s: float = 15.0,
    max_s: float = 30 * 60,
    retry_ms: int = 2000,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
    transient: Callable[[int], Awaitable[Transient]] | None = None,
    transient_interval_s: float = 0.15,
) -> AsyncIterator[bytes]:
    """The event stream of a log that only grows (agent runs today): every event after `after`,
    then each new one as `read` finds it, each with its own id, so a client that reconnects with
    Last-Event-ID gets exactly what it missed, no duplicate and no gap. Unlike `watch`, which
    resends a snapshot, this replays the log itself.

    `read(after)` returns the next events after that id (in order, possibly a page of them) and
    whether the log is final, or None when the thing is gone (a `gone` event ends the stream).
    Once the log is final and every event is sent, an `end` event (without id) tells the client
    to stop: EventSource would otherwise reconnect by itself. After `max_s` the stream just
    closes and the client resumes from its last id. `disconnected` (Starlette's
    `request.is_disconnected`) is asked before every read: a client that left stops the polling
    at once, not at the next heartbeat.

    `transient(last)` is polled every `transient_interval_s` between the reads of the log, which
    stay `interval_s` apart, and is given the id of the last log event sent. Its events go out
    as they are, after every log event read so far; `read_now` brings the next read of the log
    forward. It is never polled once the log is final."""
    yield f"retry: {retry_ms}\n\n".encode()
    started = last_sent = time.monotonic()
    last = after
    read_at = started  # when the log is read next
    pause = interval_s if transient is None else min(interval_s, transient_interval_s)
    while True:
        if disconnected is not None and await disconnected():
            return
        if transient is None or time.monotonic() >= read_at:
            page = await asyncio.to_thread(read, last)
            if page is None:
                yield b"event: gone\ndata: {}\n\n"
                return
            for item in page.events:
                yield format_event(item.id, item.event, item.data)
                last = max(last, item.id)
                last_sent = time.monotonic()
            if page.events:
                continue  # read on at once: a full page may have more behind it
            if page.final:
                yield END
                return
            read_at = time.monotonic() + interval_s
        if transient is not None:
            extra = await transient(last)
            for chunk in extra.events:
                yield chunk
                last_sent = time.monotonic()
            if extra.read_now:
                read_at = 0.0
        now = time.monotonic()
        if now - started >= max_s:
            return
        if now - last_sent >= heartbeat_s:
            last_sent = now
            yield b": ping\n\n"
        await asyncio.sleep(pause)


def response(events: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=HEADERS)
