"""app/sse.py: the event stream helper shared by sweeps and, later, agent runs."""

import asyncio
import json

from app import sse


def collect(read, **options) -> list[bytes]:  # type: ignore[no-untyped-def]
    async def go() -> list[bytes]:
        return [chunk async for chunk in sse.watch(read, **options)]

    return asyncio.run(go())


def test_sends_changes_only_then_the_final_state() -> None:
    states = iter(
        [
            sse.Snapshot("progress", {"n": 1}),
            sse.Snapshot("progress", {"n": 1}),
            sse.Snapshot("progress", {"n": 2}),
            sse.Snapshot("done", {"n": 2}, final=True),
        ]
    )
    chunks = collect(lambda: next(states), interval_s=0)
    assert chunks[0] == b"retry: 2000\n\n"
    assert chunks[1:] == [
        b'id: 1\nevent: progress\ndata: {"n":1}\n\n',
        b'id: 2\nevent: progress\ndata: {"n":2}\n\n',
        b'id: 3\nevent: done\ndata: {"n":2}\n\n',
    ]


def test_heartbeat_and_time_limit() -> None:
    chunks = collect(
        lambda: sse.Snapshot("progress", {"same": True}),
        interval_s=0.01,
        heartbeat_s=0.03,
        max_s=0.2,
    )
    assert chunks[1].startswith(b"id: 1\nevent: progress")
    assert b": ping\n\n" in chunks
    assert sum(chunk.startswith(b"id:") for chunk in chunks) == 1


def test_gone_when_read_returns_none() -> None:
    chunks = collect(lambda: None, interval_s=0)
    assert chunks[1] == b"id: 1\nevent: gone\ndata: {}\n\n"


def test_format_event_is_one_line_of_json() -> None:
    raw = sse.format_event(7, "progress", {"text": "two\nlines", "é": "ü"})
    head, data, end = raw.decode().split("\n", 2)[0], raw.decode().split("\n")[2], raw[-2:]
    assert head == "id: 7"
    assert end == b"\n\n"
    assert json.loads(data.removeprefix("data: ")) == {"text": "two\nlines", "é": "ü"}
