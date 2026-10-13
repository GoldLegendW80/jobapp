"""The server of the parser service, sandbox/server.py, against hostile and clumsy clients.

The server runs in the test process with short bounds and a stand in for the child
(stand_in_child.py), so these run on any system: they are about the protocol, the one job slot
and what becomes of a child, not about the lockdown (test_container.py, on Linux, runs the same
server with the real child and the real bounds)."""

import asyncio
import contextlib
import itertools
import json
import os
import socket
import sys
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from tests.ingestion.parser.helpers import HERE, STAND_IN, sandbox_module

server = sandbox_module("server")

MIB = 1024 * 1024
REFUSED = {"ok": False, "failure": "server", "length": 0}


def run(test: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(test(), 60))


@contextlib.asynccontextmanager
async def serving(child: tuple[str, ...] = STAND_IN, **bounds: Any) -> AsyncIterator[tuple]:
    """A server on a free loopback port, closed at the end with whatever it was running."""
    bounds.setdefault("quiet", True)
    instance = server.Server(child, **bounds)
    listener = await server.start(instance, "127.0.0.1", 0)
    try:
        yield instance, listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        await instance.close()
        await listener.wait_closed()


def line_of(kind: str = "pdf_text", size: int = 0, options: dict | None = None) -> bytes:
    return json.dumps({"kind": kind, "size": size, "options": options or {}}).encode() + b"\n"


async def exchange(port: int, payload: bytes) -> tuple[dict, bytes, bytes]:
    """Send `payload` and read an answer to its end: (header, body, what came after)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        header = json.loads(await reader.readline())
        body = await reader.readexactly(header["length"])
        return header, body, await reader.read()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def job(port: int, data: bytes = b"", kind: str = "pdf_text", **options: Any) -> dict:
    """One whole job; the child's answer, or the server's header when there is none."""
    header, body, rest = await exchange(port, line_of(kind, len(data), options) + data)
    assert rest == b"", "nothing follows the body"
    if not header["ok"]:
        assert body == b""
        return header
    assert header == {"ok": True, "failure": None, "code": "ok", "length": len(body)}
    return json.loads(body)


def no_child_left() -> bool:
    """True when this process has no child that ended and was not reaped."""
    try:
        return os.waitpid(-1, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


# --- a job, as the worker sends it --------------------------------------------------------------


def test_the_bytes_reach_the_child_and_its_answer_comes_back_framed() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            data = os.urandom(3 * MIB)
            answer = await job(port, data, kind="docx", page=3, name="é \x00")
            assert answer["received"] == len(data)
            assert answer["kind"] == "docx"
            # The options arrive as the server wrote them again, whatever characters they hold.
            assert answer["options"] == {"page": 3, "name": "é \x00"}

    run(test)


def test_the_child_gets_an_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIALS_KEY", "canary")
    monkeypatch.setenv("PARSER_CANARY", "canary")

    async def test() -> None:
        async with serving() as (_, port):
            names = (await job(port))["environ"]
            assert "CREDENTIALS_KEY" not in names
            assert "PARSER_CANARY" not in names
            assert "PATH" not in names
            assert "HOME" not in names

    run(test)


def test_every_kind_of_the_plan_is_accepted_and_no_other() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            for kind in ("pdf_text", "docx", "linkedin_zip", "image", "pdf_render", "ping"):
                assert (await job(port, b"x", kind=kind))["kind"] == kind
            for kind in ("", "PDF_TEXT", "pdf", "../main", "ping\x00", "echo"):
                header = await job(port, kind=kind)
                assert header == {**REFUSED, "code": "unknown_kind"}, kind

    run(test)


# --- the header ---------------------------------------------------------------------------------


def test_a_header_line_is_refused_over_4096_bytes() -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            pad = line_of(options={"pad": ""})
            exactly = line_of(options={"pad": "x" * (4096 - len(pad))})
            assert len(exactly) == 4096
            assert (await job(port, **json.loads(exactly)["options"]))["received"] == 0
            for length in (4097, 5000, 70_000):
                longer = line_of(options={"pad": "x" * (length - len(pad))})
                assert len(longer) == length
                header, body, _ = await exchange(port, longer)
                assert header == {**REFUSED, "code": "header_too_long"}, length
                assert body == b""
            # No newline at all: the server stops reading at its limit and answers.
            header, _, _ = await exchange(port, b"x" * 100_000)
            assert header == {**REFUSED, "code": "header_too_long"}
            assert instance.jobs == 0

    run(test)


@pytest.mark.parametrize(
    "line",
    [
        b"not json\n",
        b"\n",
        b"[]\n",
        b'"pdf_text"\n',
        b"null\n",
        b'{"size":0}\n',
        b'{"kind":"pdf_text"}\n',
        b'{"kind":7,"size":0}\n',
        b'{"kind":["pdf_text"],"size":0}\n',
        b'{"kind":"pdf_text","size":-1}\n',
        b'{"kind":"pdf_text","size":true}\n',
        b'{"kind":"pdf_text","size":1.0}\n',
        b'{"kind":"pdf_text","size":"5"}\n',
        b'{"kind":"pdf_text","size":null}\n',
        b'{"kind":"pdf_text","size":0,"options":[]}\n',
        b'{"kind":"pdf_text","size":0,"options":"x"}\n',
        b'{"kind":"pdf_text","size":0,"more":1}\n',
        b'{"kind":"pdf_text","size":0}{"kind":"ping","size":0}\n',
        b'{"kind":"pdf_text","size":0,"options":' + b"[" * 3000 + b"\n",
        b'{"kind":"pdf_\xff","size":0}\n',
        b"\xff\xfe\n",
        b'{"kind":"pdf_text","size":NaN}\n',
    ],
    ids=lambda line: repr(line[:40]),
)
def test_a_header_that_is_not_one_is_refused_and_starts_no_child(line: bytes) -> None:
    async def test() -> None:
        async with serving(child=("/nonexistent/interpreter",)) as (_, port):
            header, body, rest = await exchange(port, line + b"some bytes of a document")
            assert header == {**REFUSED, "code": "bad_request"}
            assert (body, rest) == (b"", b"")

    run(test)


def test_options_nested_as_deep_as_a_line_allows_are_answered_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4096 bytes hold about 2,000 levels. Whether the decoder follows them depends on the
    interpreter: either way the client gets an answer, and where it does not follow, a refusal."""
    deep = b'{"kind":"pdf_text","size":0,"options":{"a":' + b"[" * 1990 + b"]" * 1990 + b"}}\n"
    assert len(deep) <= 4096

    async def test() -> None:
        async with serving() as (_, port):
            header, _, _ = await exchange(port, deep)
            assert header["code"] in ("ok", "bad_request")

    run(test)

    def too_deep(*_: object, **__: object) -> None:
        raise RecursionError

    monkeypatch.setattr(server.json, "loads", too_deep)
    with pytest.raises(server.RefusedError, match="bad_request"):
        server.parse_header(deep)
    monkeypatch.undo()
    monkeypatch.setattr(server.json, "dumps", too_deep)
    with pytest.raises(server.RefusedError, match="bad_request"):
        server.parse_header(deep)


def test_a_header_cut_before_its_end_is_refused_and_an_empty_connection_is_left() -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b'{"kind":"pdf_text","si')
            writer.write_eof()
            assert json.loads(await reader.readline()) == {**REFUSED, "code": "bad_request"}
            writer.close()
            # Connected and gone without a byte: nothing to answer, nothing left behind.
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
            assert (instance.connections, instance.jobs) == (0, 0)
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


# --- the size -----------------------------------------------------------------------------------


def test_a_size_over_20_mib_is_refused_before_a_byte_of_it_is_read() -> None:
    async def test() -> None:
        async with serving(child=("/nonexistent/interpreter",)) as (instance, port):
            started = time.monotonic()
            # The header alone: the refusal comes without the server waiting for any document.
            header, _, _ = await exchange(port, line_of(size=20 * MIB + 1))
            assert header == {**REFUSED, "code": "too_large"}
            assert time.monotonic() - started < 2
            # And with a client that is already sending: the refusal still reaches it.
            header, _, _ = await exchange(port, line_of(size=20 * MIB + 1) + bytes(3 * MIB))
            assert header == {**REFUSED, "code": "too_large"}
            header, _, _ = await exchange(port, line_of(size=2**62))
            assert header == {**REFUSED, "code": "too_large"}
            # As many digits as a line can hold: a number like another, and too large.
            huge = b'{"kind":"pdf_text","size":' + b"9" * 4000 + b"}\n"
            header, _, _ = await exchange(port, huge)
            assert header == {**REFUSED, "code": "too_large"}
            assert instance.jobs == 0

    run(test)


def test_20_mib_exactly_is_a_job() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            assert (await job(port, bytes(20 * MIB)))["received"] == 20 * MIB

    run(test)


# --- silence ------------------------------------------------------------------------------------


def test_a_client_that_connects_and_sends_nothing_is_given_up() -> None:
    async def test() -> None:
        async with serving(read_timeout_s=0.3) as (instance, port):
            started = time.monotonic()
            header, _, _ = await exchange(port, b"")
            assert header == {**REFUSED, "code": "read_timeout"}
            assert 0.25 < time.monotonic() - started < 3
            await asyncio.sleep(0.05)
            assert instance.connections == 0
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


def test_a_document_that_stops_arriving_is_given_up() -> None:
    async def test() -> None:
        async with serving(read_timeout_s=0.3) as (instance, port):
            header, _, _ = await exchange(port, line_of(size=1000) + b"x" * 10)
            assert header == {**REFUSED, "code": "read_timeout"}
            await asyncio.sleep(0.05)
            assert instance.jobs == 0

    run(test)


def test_a_request_that_trickles_in_has_one_deadline_for_the_whole_of_it() -> None:
    """Never silent for long enough to be given up, and still never finished."""

    async def test() -> None:
        async with serving(read_timeout_s=0.5, request_s=0.6) as (instance, port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1000))
            started = time.monotonic()
            answer = asyncio.ensure_future(reader.readline())
            while not answer.done():
                with contextlib.suppress(ConnectionError):
                    writer.write(b"x")
                    await writer.drain()
                await asyncio.sleep(0.1)
            assert json.loads(await answer) == {**REFUSED, "code": "read_timeout"}
            assert time.monotonic() - started < 5
            writer.close()
            await asyncio.sleep(0.05)
            assert instance.jobs == 0

    run(test)


def test_a_client_that_closes_in_the_middle_of_its_document_starts_no_child(
    tmp_path: Path,
) -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            trace = tmp_path / "ran"
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1000, options={"act": "touch", "path": str(trace)}))
            writer.write(b"x" * 10)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.3)
            assert not trace.exists()
            assert (instance.connections, instance.jobs) == (0, 0)
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


# --- one job at a time --------------------------------------------------------------------------


def test_two_jobs_sent_at_once_run_one_after_the_other() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            first, second, third = await asyncio.gather(
                job(port, b"1", act="sleep", s=0.5),
                job(port, b"22", act="sleep", s=0.5),
                job(port, b"333", act="sleep", s=0.5),
            )
            spans = sorted((one["started"], one["ended"]) for one in (first, second, third))
            assert sorted(one["received"] for one in (first, second, third)) == [1, 2, 3]
            for (_, ended), (started, _) in itertools.pairwise(spans):
                assert started >= ended, "a child started while another was running"

    run(test)


def test_a_ping_is_answered_at_once_while_a_job_runs() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            long_job = asyncio.ensure_future(job(port, b"x", act="sleep", s=3))
            await asyncio.sleep(0.3)
            started = time.monotonic()
            for _ in range(3):
                assert (await job(port, kind="ping"))["kind"] == "ping"
            assert time.monotonic() - started < 2.5
            assert not long_job.done(), "the pings were answered during the job, not after it"
            assert (await long_job)["received"] == 1

    run(test)


def test_a_ping_with_bytes_waits_its_turn_like_a_job() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            long_job = asyncio.ensure_future(job(port, b"x", act="sleep", s=0.8))
            await asyncio.sleep(0.2)
            echo = await job(port, b"four", kind="ping")
            assert long_job.done()
            assert echo["received"] == 4
            assert echo["started"] >= (await long_job)["ended"]

    run(test)


def test_a_job_that_waits_longer_than_the_queue_allows_is_answered_busy() -> None:
    async def test() -> None:
        async with serving(queue_wait_s=0.3) as (instance, port):
            long_job = asyncio.ensure_future(job(port, b"x", act="sleep", s=1.5))
            await asyncio.sleep(0.2)
            started = time.monotonic()
            assert await job(port, b"y") == {**REFUSED, "code": "busy"}
            assert 0.25 < time.monotonic() - started < 1.2
            assert (await long_job)["received"] == 1
            assert instance.jobs == 0
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


def test_a_job_that_finds_the_queue_full_is_answered_busy_at_once() -> None:
    async def test() -> None:
        async with serving(max_jobs=2) as (instance, port):
            running = asyncio.ensure_future(job(port, b"x", act="sleep", s=1.0))
            waiting = asyncio.ensure_future(job(port, b"y"))
            await asyncio.sleep(0.3)
            assert instance.jobs == 2
            started = time.monotonic()
            # Its document is not read: the answer comes although nothing of it was sent.
            header, _, _ = await exchange(port, line_of(size=5 * MIB))
            assert header == {**REFUSED, "code": "busy"}
            assert time.monotonic() - started < 0.5
            assert (await running)["received"] == 1
            assert (await waiting)["received"] == 1

    run(test)


def test_connections_over_the_cap_are_answered_busy() -> None:
    async def test() -> None:
        async with serving(max_connections=2, read_timeout_s=5) as (instance, port):
            idle = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
            await asyncio.sleep(0.1)
            assert instance.connections == 2
            header, _, _ = await exchange(port, line_of("ping"))
            assert header == {**REFUSED, "code": "busy"}
            for _, writer in idle:
                writer.close()
            await asyncio.sleep(0.2)
            assert (await job(port, kind="ping"))["kind"] == "ping"

    run(test)


# --- what becomes of a child --------------------------------------------------------------------


def test_a_child_that_outlives_the_wall_clock_is_killed_and_reaped() -> None:
    async def test() -> None:
        async with serving(wall_s=0.5) as (_, port):
            started = time.monotonic()
            assert await job(port, b"x", act="sleep", s=30) == {**REFUSED, "code": "wall_limit"}
            assert 0.45 < time.monotonic() - started < 5
            assert no_child_left()
            # Closing its output does not save it: only its end does.
            header = await job(port, b"x", act="close_then_sleep", s=30)
            assert header == {**REFUSED, "code": "wall_limit"}
            assert no_child_left()
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


def test_a_child_killed_with_output_left_unread_is_reaped_and_the_job_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child past the cap is killed while it still writes, and a kill is not instant: until it
    lands the pipe fills with what nobody reads any more. Past its buffer limit asyncio stops
    reading such a pipe, so it never sees its end, and Process.wait(), which waits for the pipes
    too, waited for ever: the job never answered, the client gave up at its own deadline, and
    asyncio wrote "Task was destroyed but it is pending!" in the log of the service (issue
    #104, on a loaded machine). Here the kill lands a fifth of a second late every time. The
    answer has to come long before any limit of time: the bound below only turns a job that
    hangs into a failure."""
    kill = asyncio.subprocess.Process.kill

    def late_kill(process: asyncio.subprocess.Process) -> None:
        asyncio.get_running_loop().call_later(0.2, kill, process)

    async def test() -> None:
        async with serving(max_answer=200_000) as (_, port):
            monkeypatch.setattr(asyncio.subprocess.Process, "kill", late_kill)
            header = await asyncio.wait_for(job(port, b"x", act="flood", blocks=100_000), 10)
            monkeypatch.undo()
            assert header == {**REFUSED, "code": "output_cap"}
            assert no_child_left()
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


def test_a_child_that_writes_more_than_the_cap_is_killed() -> None:
    async def test() -> None:
        async with serving(max_answer=200_000) as (_, port):
            started = time.monotonic()
            header = await job(port, b"x", act="flood", blocks=100_000)
            assert header == {**REFUSED, "code": "output_cap"}
            assert time.monotonic() - started < 10, "killed at the cap, not at its 6 GB"
            assert no_child_left()
            # Under the cap by a block: it arrives whole.
            header, body, _ = await exchange(port, line_of(options={"act": "flood", "blocks": 3}))
            assert header["code"] == "ok"
            assert len(body) == 3 * 65536

    run(test)


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"act": "exit", "status": 70}, "sandbox"),
        ({"act": "no_read", "status": 70}, "sandbox"),
        ({"act": "exit", "status": 1}, "crashed"),
        ({"act": "exit", "status": 64}, "crashed"),
        ({"act": "signal", "name": "SIGXCPU"}, "cpu_limit"),
        ({"act": "signal", "name": "SIGKILL"}, "crashed"),
        ({"act": "signal", "name": "SIGSEGV"}, "crashed"),
        ({"act": "signal", "name": "SIGABRT"}, "crashed"),
    ],
)
def test_a_child_that_dies_is_a_code_and_the_next_job_runs(options: dict, code: str) -> None:
    """Exit status 70 is the child saying it could not lock itself down: it handled nothing,
    whatever it may have written. It may not even have read its input: 5 MiB then meet a closed
    pipe, which is the server's to survive."""

    async def test() -> None:
        async with serving() as (instance, port):
            assert await job(port, bytes(5 * MIB), **options) == {**REFUSED, "code": code}
            assert no_child_left()
            assert instance.jobs == 0
            assert not instance.slot.locked()
            assert (await job(port, b"abc"))["received"] == 3

    run(test)


def test_a_child_that_ends_without_a_word_is_an_empty_answer_for_the_client_to_refuse() -> None:
    async def test() -> None:
        async with serving() as (_, port):
            header, body, _ = await exchange(port, line_of(options={"act": "no_read"}))
            assert header == {"ok": True, "failure": None, "code": "ok", "length": 0}
            assert body == b""

    run(test)


def test_the_answer_of_a_child_is_passed_on_unread_whatever_it_is() -> None:
    """The server parses nothing: judging the child's words is the worker's part."""

    async def test() -> None:
        async with serving() as (_, port):
            for text in ("not json", "[" * 3000, '{"ok": true, "text": "a\\u0000b"}', "\ud800"):
                options = {"act": "raw", "text": text}
                header, body, _ = await exchange(port, line_of(options=options))
                assert header["code"] == "ok"
                assert body == text.encode("utf-8", "surrogatepass")

    run(test)


def test_a_child_that_cannot_be_started_is_a_code() -> None:
    async def test() -> None:
        async with serving(child=("/nonexistent/interpreter",)) as (instance, port):
            assert await job(port, b"x") == {**REFUSED, "code": "no_child"}
            assert await job(port, kind="ping") == {**REFUSED, "code": "no_child"}
            assert not instance.slot.locked()

    run(test)


# --- the client leaves --------------------------------------------------------------------------


def test_a_client_that_leaves_during_its_job_frees_the_slot_at_once() -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1, options={"act": "sleep", "s": 30}) + b"x")
            await writer.drain()
            await asyncio.sleep(0.5)
            assert instance.slot.locked()
            writer.close()
            started = time.monotonic()
            assert (await job(port, b"abc"))["received"] == 3
            assert time.monotonic() - started < 5, "the next job did not wait for the 30 s"
            assert no_child_left()

    run(test)


def test_a_client_that_leaves_while_it_waits_never_gets_a_child(tmp_path: Path) -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            trace = tmp_path / "ran"
            running = asyncio.ensure_future(job(port, b"x", act="sleep", s=0.8))
            await asyncio.sleep(0.2)
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1, options={"act": "touch", "path": str(trace)}) + b"x")
            await writer.drain()
            await asyncio.sleep(0.2)
            assert instance.jobs == 2
            writer.close()
            assert (await running)["received"] == 1
            assert (await job(port, b"abc"))["received"] == 3
            assert not trace.exists()
            assert instance.jobs == 0

    run(test)


def test_a_byte_after_the_document_cancels_the_job() -> None:
    """The client has said how many bytes it sends. One more means it is not the client this
    protocol is for: nothing is answered, and the slot is free."""

    async def test() -> None:
        async with serving() as (instance, port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1, options={"act": "sleep", "s": 30}) + b"xy")
            assert await reader.read() == b""
            writer.close()
            await asyncio.sleep(0.1)
            assert not instance.slot.locked()
            assert no_child_left()

    run(test)


def test_stopping_the_server_kills_the_child_it_was_running() -> None:
    async def test() -> None:
        async with serving() as (instance, port):
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(line_of(size=1, options={"act": "sleep", "s": 30}) + b"x")
            await writer.drain()
            await asyncio.sleep(0.5)
            started = time.monotonic()
            await instance.close()
            assert time.monotonic() - started < 5
            assert no_child_left()
            writer.close()

    run(test)


# --- what the server writes down ----------------------------------------------------------------


def test_the_log_holds_kinds_sizes_and_codes_and_nothing_of_a_document(
    capfd: pytest.CaptureFixture[str],
) -> None:
    secret = b"PRIVATE-document-canary-5fd1"

    async def test() -> None:
        async with serving(quiet=False, wall_s=0.4) as (_, port):
            await job(port, secret * 100, kind="docx", name="PRIVATE-option-canary")
            await job(port, secret, act="sleep", s=30, name="PRIVATE-option-canary")
            await exchange(port, b'{"kind":"PRIVATE-kind-canary","size":0}\n')
            await exchange(port, b"PRIVATE-line-canary\n")
            await job(port, kind="ping")

    run(test)
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "PRIVATE" not in captured.err
    lines = [json.loads(line) for line in captured.err.splitlines()]
    events = [{key: line[key] for key in line if key not in ("ts", "ms")} for line in lines]
    assert events == [
        {"event": "job", "kind": "docx", "size": 2800, "code": "ok", "out": events[0]["out"]},
        {"event": "job", "kind": "pdf_text", "size": 28, "code": "wall_limit", "out": 0},
        {"event": "refused", "code": "unknown_kind"},
        {"event": "refused", "code": "bad_request"},
    ], "a ping that worked is not logged: the healthcheck sends one every 15 s"


# --- the healthcheck and the command line -------------------------------------------------------

GOOD_REPORT = {
    "seccomp": 2,
    "filters": 2,
    "no_new_privs": 1,
    "dumpable": 0,
    "capabilities": 0,
    "limits": dict(server.EXPECTED_LIMITS),
    "denied": dict.fromkeys(server.REQUIRED_DENIED, "EPERM"),
    "isolated": True,
    "environ": 0,
    "uid": 10002,
    "descriptors": [0, 1, 2],
    "sockets": 0,
    "writable": [],
    "parent_hidden": True,
    "cryptography_blocked": True,
    "machine": "aarch64",
    "kernel": "5.15.148-tegra",
}

DEFECTS = [
    ({"seccomp": 0}, ["seccomp_mode"]),
    ({"seccomp": True}, ["seccomp_mode"]),
    ({"denied": {**GOOD_REPORT["denied"], "socket": "ALLOWED"}}, ["allowed:socket"]),
    ({"denied": {**GOOD_REPORT["denied"], "kill": "ESRCH"}}, ["allowed:kill"]),
    (
        {"denied": {k: v for k, v in GOOD_REPORT["denied"].items() if k != "io_uring_setup"}},
        ["allowed:io_uring_setup"],
    ),
    ({"denied": "EPERM"}, [f"allowed:{name}" for name in server.REQUIRED_DENIED]),
    ({"limits": {**server.EXPECTED_LIMITS, "nproc": 4096}}, ["limits"]),
    ({"limits": {**server.EXPECTED_LIMITS, "as": -1}}, ["limits"]),
    ({"limits": {k: v for k, v in server.EXPECTED_LIMITS.items() if k != "fsize"}}, ["limits"]),
    ({"no_new_privs": 0}, ["no_new_privs"]),
    ({"dumpable": 1}, ["dumpable"]),
    ({"dumpable": False}, ["dumpable"]),
    ({"capabilities": 0x00000000A80425FB}, ["capabilities"]),
    ({"isolated": False}, ["isolated"]),
    ({"isolated": 1}, ["isolated"]),
    ({"environ": 31}, ["environ"]),
    ({"sockets": 1}, ["sockets"]),
    ({"descriptors": [0, 1, 2, 5]}, ["descriptors"]),
    ({"writable": ["/dev/mqueue"]}, ["writable"]),
    ({"parent_hidden": False}, ["parent_hidden"]),
    ({"cryptography_blocked": False}, ["cryptography_blocked"]),
    ({"uid": 0}, ["uid"]),
    ({"uid": "10002"}, ["uid"]),
]


def test_a_report_of_a_locked_down_child_has_no_problem() -> None:
    assert server.sandbox_problems(GOOD_REPORT) == []
    assert server.sandbox_problems({**GOOD_REPORT, "filters": -1, "kernel": "x"}) == []


@pytest.mark.parametrize(("change", "problems"), DEFECTS)
def test_every_single_defect_of_a_report_is_a_problem(change: dict, problems: list[str]) -> None:
    assert server.sandbox_problems({**GOOD_REPORT, **change}) == problems


@pytest.mark.parametrize("report", [None, [], "ok", 2, {}])
def test_no_report_is_a_problem(report: object) -> None:
    assert server.sandbox_problems(report)


def checking(tmp_path: Path, answer: bytes) -> list[str]:
    """What the healthcheck says of a service whose ping child answers `answer`."""
    path = tmp_path / "answer"
    path.write_bytes(answer)
    found: list[str] = []

    async def test() -> None:
        async with serving(child=(*STAND_IN, "--answer", str(path))) as (_, port):
            found.extend(await asyncio.to_thread(server.check, port))

    run(test)
    return found


def test_the_healthcheck_passes_on_a_child_that_reports_the_sandbox_in_force(
    tmp_path: Path,
) -> None:
    answer = {"ok": True, "kind": "ping", "sandbox": GOOD_REPORT}
    assert checking(tmp_path, json.dumps(answer).encode()) == []


@pytest.mark.parametrize(
    ("answer", "problems"),
    [
        (b"", ["bad_length"]),
        (b"not json", ["bad_answer"]),
        (b"[" * 100_000, ["bad_answer"]),
        (b"[]", ["bad_answer"]),
        (b'{"ok": false, "failure": "server", "code": "not_supported"}', ["bad_answer"]),
        (b'{"ok": true}', ["no_report"]),
        (
            json.dumps({"ok": True, "sandbox": {**GOOD_REPORT, "seccomp": 0}}).encode(),
            ["seccomp_mode"],
        ),
    ],
)
def test_the_healthcheck_fails_on_anything_else(
    tmp_path: Path, answer: bytes, problems: list[str]
) -> None:
    assert checking(tmp_path, answer) == problems


def test_the_healthcheck_fails_when_the_child_cannot_lock_itself_down() -> None:
    """The simulation of a kernel without the filter: the child exits with 70 and the service
    is unhealthy, which is what stops a deploy."""
    found: list[str] = []

    async def test() -> None:
        child = (sys.executable, "-c", "import sys; sys.exit(70)")
        async with serving(child=child) as (_, port):
            found.extend(await asyncio.to_thread(server.check, port))

    run(test)
    assert found == ["refused:sandbox"]


def test_the_healthcheck_fails_when_nothing_listens() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert server.check(port, timeout_s=1) == ["unreachable"]


def test_the_healthcheck_does_not_trust_a_server_that_is_not_this_one() -> None:
    """Something else on the port: garbage, a header without a body, a refusal with a made up
    code. Each is unhealthy, none raises."""
    found = []

    async def test() -> None:
        for reply in (b"HTTP/1.1 200 OK\r\n\r\n", b'{"ok":true,"length":10}\n', b"\n", b""):

            async def answer(reader, writer, reply=reply) -> None:
                await reader.readline()
                writer.write(reply)
                writer.close()

            listener = await asyncio.start_server(answer, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            found.append(await asyncio.to_thread(server.check, port))
            listener.close()
            await listener.wait_closed()
        for code in ("busy", "invented <b>code</b>"):

            async def refuse(reader, writer, code=code) -> None:
                await reader.readline()
                writer.write(json.dumps({"ok": False, "code": code, "length": 0}).encode() + b"\n")
                writer.close()

            listener = await asyncio.start_server(refuse, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            found.append(await asyncio.to_thread(server.check, port))
            listener.close()
            await listener.wait_closed()

    run(test)
    assert found == [
        ["bad_answer"],
        ["bad_answer"],
        ["bad_answer"],
        ["bad_answer"],
        ["refused:busy"],
        ["refused"],
    ]


def test_the_command_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("PARSER_PORT", str(port))
    assert server.main(["server.py", "--check"]) == 1
    assert capsys.readouterr().err == "unhealthy: unreachable\n"
    assert server.main(["server.py", "--serve-everything"]) == 2
    assert "python -I /app/app/ingestion/sandbox/server.py" in capsys.readouterr().err
    for value in ("", "0", "70000", "-1", "seven", "7070 "):
        monkeypatch.setenv("PARSER_PORT", value)
        if value == "7070 ":
            assert server.port_from(dict(os.environ)) == 7070
            continue
        with pytest.raises(SystemExit, match="PARSER_PORT"):
            server.main(["server.py", "--check"])
    assert server.port_from({}) == 7070


def test_the_command_line_check_passes_against_a_healthy_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "answer"
    path.write_text(json.dumps({"ok": True, "kind": "ping", "sandbox": GOOD_REPORT}))
    status = []

    async def test() -> None:
        async with serving(child=(*STAND_IN, "--answer", str(path))) as (_, port):
            monkeypatch.setenv("PARSER_PORT", str(port))
            status.append(await asyncio.to_thread(server.main, ["server.py", "--check"]))

    run(test)
    assert status == [0]


class RecordingLibc:
    def __init__(self, result: int = 0) -> None:
        self.calls: list[tuple[int, ...]] = []
        self.result = result

    def prctl(self, *arguments: int) -> int:
        self.calls.append(arguments)
        return self.result


class RecordingResource:
    RLIMIT_CORE = 4

    def __init__(self) -> None:
        self.limits: dict[int, tuple[int, int]] = {}

    def setrlimit(self, which: int, value: tuple[int, int]) -> None:
        self.limits[which] = value


def test_the_server_makes_itself_non_dumpable_and_refuses_to_run_as_root() -> None:
    libc, res = RecordingLibc(), RecordingResource()
    server.lock_down_server(libc, res, uid=10002)
    assert libc.calls == [(server.PR_SET_DUMPABLE, 0, 0, 0, 0)]
    assert res.limits == {res.RLIMIT_CORE: (0, 0)}
    with pytest.raises(SystemExit, match="root"):
        server.lock_down_server(RecordingLibc(), RecordingResource(), uid=0)
    with pytest.raises(SystemExit, match="PR_SET_DUMPABLE"):
        server.lock_down_server(RecordingLibc(result=-1), RecordingResource(), uid=10002)


def test_the_child_is_started_isolated_from_the_absolute_path_of_main() -> None:
    interpreter, *flags, script = server.CHILD
    assert interpreter == sys.executable
    assert flags == ["-I", "-B"]
    assert Path(script).is_absolute()
    assert Path(script).name == "main.py"
    assert Path(script).parent == Path(server.HERE)
    assert Path(server.HERE) != HERE, "the tests' own directory holds no file of the service"
