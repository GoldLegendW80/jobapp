"""Server-sent events: one helper for every live view (sweep progress today, agent runs later).

`watch(read, ...)` turns a function that reads the current state of something into an event
stream: it reads every `interval_s`, sends an event each time the state changed (the first read
always), a comment line every `heartbeat_s` so proxies keep the connection open, and ends after
the state says it is final or after `max_s`. Each event carries an increasing `id`; a client
that reconnects (EventSource does so by itself, after `retry_ms`) gets the current state again
first, so nothing is lost by resuming from scratch.

`response(events)` wraps a stream in a StreamingResponse with the headers live streams need:
text/event-stream, no caching, no transformation (Caddy compresses text/* otherwise, which would
hold events back) and no proxy buffering.

Reads go through `asyncio.to_thread`, so a synchronous SQLAlchemy read never blocks the loop.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
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


def response(events: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=HEADERS)
