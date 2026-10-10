"""app/sse.py `replay`: a log that only grows, streamed once per event and resumed after the
client's Last-Event-ID."""

import asyncio
from collections.abc import Callable

from app import sse


def collect(read: Callable[[int], sse.Page | None], **options: float | int) -> list[bytes]:
    async def go() -> list[bytes]:
        return [chunk async for chunk in sse.replay(read, **options)]  # type: ignore[arg-type]

    return asyncio.run(go())


class Log:
    """A log of `total` events that become visible one per `appear_every` reads, read `page` at a
    time, final once all are visible."""

    def __init__(self, total: int, page: int = 2, appear_every: int = 1) -> None:
        self.total, self.page, self.appear_every = total, page, appear_every
        self.reads: list[int] = []

    def __call__(self, after: int) -> sse.Page:
        self.reads.append(after)
        visible = min(self.total, len(self.reads) // self.appear_every + 1)
        ids = list(range(after + 1, visible + 1))[: self.page]
        events = [sse.Event(i, "step", {"n": i}) for i in ids]
        return sse.Page(events, final=visible == self.total and after + len(ids) >= self.total)


def ids_of(chunks: list[bytes]) -> list[int]:
    return [int(c.split(b"\n")[0][4:]) for c in chunks if c.startswith(b"id: ")]


def test_each_event_once_in_order_then_end() -> None:
    chunks = collect(Log(total=7, page=2, appear_every=2), interval_s=0)
    assert chunks[0] == b"retry: 2000\n\n"
    assert ids_of(chunks) == [1, 2, 3, 4, 5, 6, 7]
    assert chunks[1] == b'id: 1\nevent: step\ndata: {"n":1}\n\n'
    assert chunks[-1] == sse.END
    # The end event carries no id: it never moves the client's Last-Event-ID.
    assert b"id:" not in sse.END


def test_resumes_after_the_given_id() -> None:
    log = Log(total=5, page=10)
    assert ids_of(collect(log, after=3, interval_s=0)) == [4, 5]
    assert log.reads[0] == 3
    assert ids_of(collect(Log(total=5, page=10), after=5, interval_s=0)) == []
    assert ids_of(collect(Log(total=5, page=10), after=99, interval_s=0)) == []


def test_gone() -> None:
    assert collect(lambda after: None, interval_s=0)[1] == b"event: gone\ndata: {}\n\n"


def test_heartbeat_and_time_limit_without_end() -> None:
    chunks = collect(
        lambda after: sse.Page([], final=False), interval_s=0.01, heartbeat_s=0.03, max_s=0.2
    )
    assert b": ping\n\n" in chunks
    # Cut by max_s: no `end`, so the client reconnects and resumes from its last id.
    assert sse.END not in chunks


def test_last_event_id_parsing() -> None:
    assert sse.last_event_id(None) == 0
    assert sse.last_event_id("12") == 12
    assert sse.last_event_id(" 12 ", "3") == 12  # the header wins over the query
    assert sse.last_event_id(None, "3") == 3
    assert sse.last_event_id("", "3") == 3
    for bad in ("-1", "1.5", "abc", "1e3", "9" * 19):
        assert sse.last_event_id(bad) == 0, bad
    # str.isdigit accepts other scripts' digits; int() reads them too, which is harmless.
    assert sse.last_event_id("٣") == 3


def test_stops_as_soon_as_the_client_left() -> None:
    reads: list[int] = []

    def read(after: int) -> sse.Page:
        reads.append(after)
        return sse.Page([], final=False)

    calls = iter([False, False, True])

    async def disconnected() -> bool:
        return next(calls)

    chunks = collect(read, interval_s=0, disconnected=disconnected)  # type: ignore[arg-type]
    assert chunks == [b"retry: 2000\n\n"]
    assert len(reads) == 2  # no read once the client has left
