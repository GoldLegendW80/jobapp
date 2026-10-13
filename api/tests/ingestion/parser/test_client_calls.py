"""The worker's client over a connection: against servers that lie, stall and vanish, and
against the real server of the service run in the test process (with a stand in for the child,
so on any system). What each failure becomes for the engine, how fast, and what is logged."""

import asyncio
import contextlib
import json
import logging
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion.parser.helpers import STAND_IN, BackgroundServer, good_ping

MIB = 1024 * 1024
Reply = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def run(test: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(test(), 60))


@contextlib.asynccontextmanager
async def lying(reply: Reply | bytes) -> AsyncIterator[int]:
    """A server that reads a request and then does what `reply` does (or sends those bytes and
    closes). Yields its port."""

    handlers: set[asyncio.Task] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
                line = json.loads(await reader.readline())
                await reader.readexactly(line["size"])
                if isinstance(reply, bytes):
                    writer.write(reply)
                    await writer.drain()
                else:
                    await reply(reader, writer)
        finally:
            writer.close()

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        # A reply that stalls on purpose is not waited for once the test has its answer.
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await listener.wait_closed()


def framed(body: bytes | dict, **header: Any) -> bytes:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    line = {"ok": True, "failure": None, "code": "ok", "length": len(raw), **header}
    return json.dumps(line).encode() + b"\n" + raw


async def parse(port: int, data: bytes = b"%PDF-", **more: Any) -> pc.Parsed:
    return await pc.parse("pdf_text", data, host="127.0.0.1", port=port, **more)


async def error_of(port: int, data: bytes = b"%PDF-", **more: Any) -> pc.ParserError:
    with pytest.raises(pc.ParserError) as raised:
        await parse(port, data, **more)
    return raised.value


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --- an answer that passes ----------------------------------------------------------------------


def test_a_good_answer_is_a_result() -> None:
    async def test() -> None:
        answer = {"ok": True, "text": "a\fb", "pages": 2, "pages_text": 2, "pages_image": 0}
        async with lying(framed(answer)) as port:
            parsed = await parse(port)
            assert parsed == pc.Parsed(text="a\fb", pages=2, pages_text=2, pages_image=0)

    run(test)


def test_an_answer_of_4_mib_arrives_whole() -> None:
    async def test() -> None:
        text = "é" * 250_000 + "x" * 250_000
        padding = " " * (pc.MAX_ANSWER - len(json.dumps({"ok": True, "text": text}).encode()))
        body = (json.dumps({"ok": True, "text": text}) + padding).encode()
        assert len(body) == pc.MAX_ANSWER
        async with lying(framed(body)) as port:
            assert (await parse(port)).text == text

    run(test)


# --- answers that lie ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "rule"),
    [
        (b"not json\n", "not_json"),
        (b"HTTP/1.1 400 Bad Request\r\n\r\n", "not_json"),
        (b"x" * 5000 + b"\n", "header_too_long"),
        (b"x" * 100_000, "header_too_long"),
        (b'{"ok":true,"failure":null,"code":"ok","length":10', "truncated"),
        (framed(b"0123456789", length=4 * MIB + 1), "length"),
        (framed(b"0123456789", length=-1), "length"),
        (framed(b"0123456789", length=20), "truncated"),
        (framed(b"0123456789", length=5), "trailing_bytes"),
        (framed({"ok": True}) + b"\n", "trailing_bytes"),
        (framed({"ok": True}) + framed({"ok": True}), "trailing_bytes"),
        (framed(b""), "not_json"),
        (framed(b"not json"), "not_json"),
        (framed(b"\xff\xfe"), "not_utf8"),
        (framed(b"[" * 2_000_000), "too_deep"),
        (framed([{"ok": True}]), "ok"),
        (framed({"ok": True, "pages": -3, "pages_text": 0, "pages_image": 0}), "pages"),
        (framed({"ok": True, "text": "a\x00b"}), "nul"),
        (framed(b'{"ok": true, "text": "a\\ud800b"}'), "surrogate"),
        (
            framed({"ok": True, "text": "a\fb\fc", "pages": 2, "pages_text": 2, "pages_image": 0}),
            "form_feeds",
        ),
        (
            framed(
                {
                    "ok": True,
                    "images": [{"position": 0, "width": 9, "height": 9, "jpeg": "iVBORw0KGgo="}],
                }
            ),
            "image_not_jpeg",
        ),
        (framed({"ok": True, "notes": [{"code": "Pages 3 to 9 were not read."}]}), "note_code"),
        (framed({"ok": True, "text": "x" * 500_001}), "text_too_long"),
        (framed(b'{"ok": true, "ok": false, "failure": "file", "code": "bomb"}'), "duplicate_key"),
        (framed({"ok": True, "kind": "ping"}), "kind"),
    ],
)
def test_a_malformed_answer_is_a_failure_of_the_server_and_gives_nothing(
    reply: bytes, rule: str
) -> None:
    """Each of these is an answer a child taken over, or anything else on that port, could
    send. The call raises, so there is no result to store."""

    async def test() -> None:
        async with lying(reply) as port:
            error = await error_of(port)
            assert type(error) is pc.ParserMalformedError
            assert (error.code, error.failure) == (rule, "server")

    run(test)


def test_an_answer_nested_10000_deep_is_malformed_whatever_the_decoder_makes_of_it() -> None:
    async def test() -> None:
        async with lying(framed(b"[" * 10_000 + b"]" * 10_000)) as port:
            error = await error_of(port)
            assert type(error) is pc.ParserMalformedError
            assert error.code in ("ok", "too_deep")

    run(test)


def test_an_announced_length_over_4_mib_is_refused_before_any_of_it_is_read() -> None:
    async def test() -> None:
        sent = []

        async def flood(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(framed(b"", length=1024 * MIB))
            block = b"x" * 65536
            for _ in range(20_000):  # 1.3 GB, if anyone were reading
                writer.write(block)
                await writer.drain()
                sent.append(len(block))

        async with lying(flood) as port:
            started = time.monotonic()
            error = await error_of(port)
            assert (type(error), error.code) == (pc.ParserMalformedError, "length")
            assert time.monotonic() - started < 5
        assert sum(sent) < 64 * MIB, "the client left instead of reading what was announced"

    run(test)


def test_a_server_that_keeps_the_connection_open_after_its_answer_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pc, "CLOSE_TIMEOUT_S", 0.3)

    async def test() -> None:
        async def linger(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(framed({"ok": True}))
            await writer.drain()
            await asyncio.sleep(5)

        async with lying(linger) as port:
            started = time.monotonic()
            error = await error_of(port)
            assert (type(error), error.code) == (pc.ParserMalformedError, "not_closed")
            assert time.monotonic() - started < 3

    run(test)


# --- answers that say no ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "error", "failure", "code"),
    [
        (
            framed(b"", ok=False, failure="server", code="busy"),
            pc.ParserBusyError,
            "server",
            "busy",
        ),
        (
            framed(b"", ok=False, failure="server", code="wall_limit"),
            pc.ParserTimeoutError,
            "server",
            "wall_limit",
        ),
        (
            framed(b"", ok=False, failure="server", code="cpu_limit"),
            pc.ParserTimeoutError,
            "server",
            "cpu_limit",
        ),
        (
            framed(b"", ok=False, failure="server", code="crashed"),
            pc.ParserRefusedError,
            "server",
            "crashed",
        ),
        (
            framed(b"", ok=False, failure="server", code="sandbox"),
            pc.ParserRefusedError,
            "server",
            "sandbox",
        ),
        (
            framed({"ok": False, "failure": "server", "code": "not_supported"}),
            pc.ParserRefusedError,
            "server",
            "not_supported",
        ),
        (
            framed({"ok": False, "failure": "server", "code": "memory_limit"}),
            pc.ParserRefusedError,
            "server",
            "memory_limit",
        ),
        (
            framed({"ok": False, "failure": "file", "code": "protected"}),
            pc.ParserRefusedError,
            "file",
            "protected",
        ),
        (
            framed({"ok": False, "failure": "file", "code": "unreadable"}),
            pc.ParserRefusedError,
            "file",
            "unreadable",
        ),
    ],
)
def test_a_refusal_is_its_error_with_whose_failure_it_is(
    reply: bytes, error: type[pc.ParserError], failure: str, code: str
) -> None:
    async def test() -> None:
        async with lying(reply) as port:
            raised = await error_of(port)
            assert type(raised) is error
            assert (raised.failure, raised.code) == (failure, code)

    run(test)


# --- nobody there -------------------------------------------------------------------------------


def test_a_parser_that_is_stopped_fails_fast_as_down() -> None:
    """The worker stays healthy without the parser: the job fails at once, as a failure of the
    server, and the reaper tries it once more later."""

    async def test() -> None:
        started = time.monotonic()
        error = await error_of(free_port())
        assert (type(error), error.code, error.failure) == (
            pc.ParserDownError,
            "unreachable",
            "server",
        )
        # The name of a service that is not running does not resolve.
        with pytest.raises(pc.ParserDownError) as raised:
            await pc.parse("pdf_text", b"x", host="parser.invalid", port=7070)
        assert raised.value.code == "unreachable"
        assert time.monotonic() - started < 8

    run(test)


def test_a_parser_that_does_not_take_the_connection_is_down_after_5_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pc, "CONNECT_TIMEOUT_S", 0.3)

    async def test() -> None:
        started = time.monotonic()
        with pytest.raises(pc.ParserDownError) as raised:
            # TEST-NET-1: packets are dropped, only the timeout ends the attempt.
            await pc.parse("pdf_text", b"x", host="192.0.2.1", port=7070)
        assert raised.value.code == "unreachable"
        assert time.monotonic() - started < 3

    run(test)


def test_a_connection_closed_without_a_word_is_down() -> None:
    async def test() -> None:
        async with lying(b"") as port:
            error = await error_of(port)
            assert (type(error), error.code) == (pc.ParserDownError, "closed")

        async def reset(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.transport.abort()  # a reset, as from a container killed mid job

        async with lying(reset) as port:
            error = await error_of(port)
            assert (type(error), error.code) == (pc.ParserDownError, "closed")

    run(test)


def test_a_parser_that_never_answers_is_a_timeout_at_the_clients_deadline() -> None:
    async def test() -> None:
        async def silent(_: asyncio.StreamReader, __: asyncio.StreamWriter) -> None:
            await asyncio.sleep(30)

        async with lying(silent) as port:
            started = time.monotonic()
            error = await error_of(port, timeout_s=0.4)
            assert (type(error), error.code, error.failure) == (
                pc.ParserTimeoutError,
                "deadline",
                "server",
            )
            assert 0.35 < time.monotonic() - started < 3

    run(test)


def test_an_answer_that_trickles_in_does_not_outlast_the_deadline() -> None:
    async def test() -> None:
        async def trickle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            for byte in framed({"ok": True, "text": "x" * 1000}):
                writer.write(bytes([byte]))
                await writer.drain()
                await asyncio.sleep(0.05)

        async with lying(trickle) as port:
            started = time.monotonic()
            error = await error_of(port, timeout_s=0.5)
            assert (type(error), error.code) == (pc.ParserTimeoutError, "deadline")
            assert time.monotonic() - started < 3

    run(test)


def test_the_deadline_of_a_call_is_75_s_and_longer_than_a_child_lives() -> None:
    assert pc.TIMEOUT_S == 75.0
    assert pc.CONNECT_TIMEOUT_S == 5.0
    assert pc.parse.__kwdefaults__ == {"host": "parser", "port": 7070, "timeout_s": 75.0}


# --- what the caller got wrong never reaches the network ----------------------------------------


def test_a_document_over_20_mib_is_refused_without_a_connection() -> None:
    async def test() -> None:
        started = time.monotonic()
        error = await error_of(free_port(), bytes(20 * MIB + 1))
        assert (type(error), error.code) == (pc.ParserRefusedError, "too_large")
        assert time.monotonic() - started < 1

    run(test)


def test_a_call_the_code_got_wrong_is_a_value_error() -> None:
    async def test() -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            await pc.parse("rtf", b"x", host="127.0.0.1", port=free_port())
        with pytest.raises(ValueError, match="not a parse"):
            await pc.parse("ping", b"x", host="127.0.0.1", port=free_port())
        with pytest.raises(ValueError, match="do not fit"):
            await pc.parse("docx", b"x", {"pad": "x" * 5000}, host="127.0.0.1", port=free_port())

    run(test)
    assert pc.request_line("docx", 3, {"from": 0, "to": 7}) == (
        b'{"kind":"docx","size":3,"options":{"from":0,"to":7}}\n'
    )
    # ASCII on the wire, whatever the options hold: no line break can end the header early.
    assert pc.request_line("docx", 0, {"name": "é\n "}).count(b"\n") == 1


# --- against the real server --------------------------------------------------------------------


def raw_answer(text: str) -> dict:
    return {"act": "raw", "text": text}


def test_through_the_real_server_a_result_a_refusal_and_a_malformed_answer() -> None:
    with BackgroundServer() as background:

        async def test() -> None:
            port = background.port
            counts = {"pages": 2, "pages_text": 1, "pages_image": 1}
            good = json.dumps({"ok": True, "text": "é\f", **counts})
            parsed = await parse(port, bytes(MIB), options=raw_answer(good))
            assert parsed == pc.Parsed(text="é\f", pages=2, pages_text=1, pages_image=1)

            protected = json.dumps({"ok": False, "failure": "file", "code": "protected"})
            error = await error_of(port, options=raw_answer(protected))
            assert (type(error), error.failure, error.code) == (
                pc.ParserRefusedError,
                "file",
                "protected",
            )
            for text, rule in (
                ("", "not_json"),
                ("not json", "not_json"),
                ('{"ok": true, "text": "a\\u0000b"}', "nul"),
                ('{"ok": true, "text": "\\udc00"}', "surrogate"),
                ('{"ok": true, "notes": [{"code": "call this number"}]}', "note_code"),
            ):
                error = await error_of(port, options=raw_answer(text))
                assert (type(error), error.code) == (pc.ParserMalformedError, rule), text
            # The stand in's own answer has keys this client does not know: malformed too.
            error = await error_of(port, options={})
            assert type(error) is pc.ParserMalformedError

        run(test)


def test_through_the_real_server_every_way_a_child_ends_is_its_error() -> None:
    with BackgroundServer(wall_s=0.5, max_answer=100_000) as background:

        async def test() -> None:
            port = background.port
            for options, kind, code in (
                ({"act": "sleep", "s": 30}, pc.ParserTimeoutError, "wall_limit"),
                ({"act": "signal", "name": "SIGXCPU"}, pc.ParserTimeoutError, "cpu_limit"),
                ({"act": "signal", "name": "SIGSEGV"}, pc.ParserRefusedError, "crashed"),
                ({"act": "exit", "status": 70}, pc.ParserRefusedError, "sandbox"),
                ({"act": "flood", "blocks": 100}, pc.ParserRefusedError, "output_cap"),
            ):
                error = await error_of(port, options=options)
                assert (type(error), error.code, error.failure) == (kind, code, "server")
            answer = json.dumps({"ok": True, "text": "still here"})
            assert (await parse(port, options=raw_answer(answer))).text == "still here"

        run(test)


def test_through_the_real_server_a_second_job_waits_and_a_late_one_is_busy() -> None:
    with BackgroundServer(queue_wait_s=0.4) as background:

        async def test() -> None:
            port = background.port
            answer = json.dumps({"ok": True, "text": "x"})
            slow = asyncio.ensure_future(error_of(port, options={"act": "sleep", "s": 1.5}))
            await asyncio.sleep(0.3)
            error = await error_of(port, options=raw_answer(answer))
            assert (type(error), error.code, error.failure) == (
                pc.ParserBusyError,
                "busy",
                "server",
            )
            await slow
            assert (await parse(port, options=raw_answer(answer))).text == "x"

        run(test)


def test_a_parser_stopped_in_the_middle_of_a_job_is_down_and_back_after_its_restart() -> None:
    """Restarted mid job: the connection ends without an answer. The client says so at once,
    not at its deadline, and the next call finds the new parser."""
    first = BackgroundServer()
    with first:

        async def interrupted() -> None:
            call = asyncio.ensure_future(error_of(first.port, options={"act": "sleep", "s": 30}))
            await asyncio.sleep(0.5)
            started = time.monotonic()
            await asyncio.to_thread(first.stop)
            error = await call
            assert (type(error), error.code, error.failure) == (
                pc.ParserDownError,
                "closed",
                "server",
            )
            assert time.monotonic() - started < 10
            # Stopped: nobody listens any more.
            error = await error_of(first.port)
            assert (type(error), error.code) == (pc.ParserDownError, "unreachable")

        run(interrupted)
    with BackgroundServer() as second:

        async def restarted() -> None:
            answer = json.dumps({"ok": True, "text": "back"})
            assert (await parse(second.port, options=raw_answer(answer))).text == "back"

        run(restarted)


# --- ping ---------------------------------------------------------------------------------------


def ping_child(tmp_path: Path, answer: dict) -> tuple[str, ...]:
    path = tmp_path / "ping.json"
    path.write_text(json.dumps(answer), encoding="utf-8")
    return (*STAND_IN, "--ping", str(path))


def test_a_ping_reports_the_sandbox_and_echoes_what_it_was_sent(tmp_path: Path) -> None:
    with BackgroundServer(ping_child(tmp_path, good_ping())) as background:

        async def test() -> None:
            report = await pc.ping(host="127.0.0.1", port=background.port)
            assert report.in_force
            assert report.received == 0
            data = bytes(range(256)) * 4096  # 1 MiB
            report = await pc.ping(data, host="127.0.0.1", port=background.port)
            assert report.received == len(data)
            assert report.in_force

        run(test)


def test_a_ping_whose_echo_is_wrong_is_malformed(tmp_path: Path) -> None:
    """The count and the hash are of the bytes the child received. An answer that reports
    others did not come from a child that read what was sent."""
    path = tmp_path / "ping.json"
    path.write_text(json.dumps(good_ping(b"other bytes")), encoding="utf-8")
    with BackgroundServer((*STAND_IN, "--answer", str(path))) as background:

        async def test() -> None:
            with pytest.raises(pc.ParserMalformedError) as raised:
                await pc.ping(b"these bytes", host="127.0.0.1", port=background.port)
            assert raised.value.code == "echo"

        run(test)


def test_the_command_line_pings_and_says_in_one_line_whether_the_sandbox_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with BackgroundServer(ping_child(tmp_path, good_ping())) as background:
        port = ["--host", "127.0.0.1", "--port", str(background.port)]
        assert pc.main(["ping", *port]) == 0
        line = json.loads(capsys.readouterr().out)
        assert line.pop("ms") >= 0
        assert line == {
            "ok": True,
            "problems": [],
            "bytes": 0,
            "child_cpu_ms": 49,
            "child_max_rss_kb": 26308,
            "seccomp": 2,
            "filters": 2,
            "denied": 25,
            "machine": "aarch64",
            "kernel": "5.15.148-tegra",
        }
        assert pc.main(["ping", "--bytes", "65536", *port]) == 0
        assert json.loads(capsys.readouterr().out)["bytes"] == 65536

    leaky = good_ping()
    leaky["sandbox"]["denied"]["socket"] = "ALLOWED"
    leaky["sandbox"]["seccomp"] = 0
    with BackgroundServer(ping_child(tmp_path, leaky)) as background:
        assert pc.main(["ping", "--host", "127.0.0.1", "--port", str(background.port)]) == 1
        line = json.loads(capsys.readouterr().out)
        assert line["ok"] is False
        assert line["problems"] == ["seccomp_mode", "allowed:socket"]
        assert line["denied"] == 24

    assert pc.main(["ping", "--host", "127.0.0.1", "--port", str(free_port())]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "ParserDownError",
        "code": "unreachable",
    }
    for arguments in (["ping", "--bytes", "-1"], ["ping", "--bytes", "20971521"], [], ["parse"]):
        with pytest.raises(SystemExit) as exit_:
            pc.main(arguments)
        assert exit_.value.code == 2
    capsys.readouterr()


# --- what is logged -----------------------------------------------------------------------------


def test_one_line_per_call_with_the_kind_the_size_and_the_outcome_and_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = b"PRIVATE-document-canary-91c2 " * 1000
    with BackgroundServer(wall_s=0.5) as background, caplog.at_level(logging.DEBUG):

        async def test() -> None:
            port = background.port
            for text in (
                json.dumps({"ok": True, "text": "PRIVATE-text-canary"}),
                json.dumps({"ok": True, "text": "PRIVATE-text-canary\x00"}),
                json.dumps({"ok": True, "notes": [{"code": "PRIVATE-note-canary"}]}),
                json.dumps({"ok": False, "failure": "file", "code": "PRIVATE-code-canary"}),
                json.dumps({"ok": False, "failure": "file", "code": "protected"}),
                "PRIVATE-garbage-canary",
            ):
                options = {"act": "raw", "text": text, "name": "PRIVATE-option-canary"}
                with contextlib.suppress(pc.ParserError):
                    await parse(port, document, options=options)
            with contextlib.suppress(pc.ParserError):
                await parse(port, document, options={"act": "sleep", "s": 30})
            with contextlib.suppress(pc.ParserError):
                await parse(free_port(), document)

        run(test)

    records = [record for record in caplog.records if record.name == pc.logger.name]
    assert [(record.kind, record.size, record.outcome) for record in records] == [
        ("pdf_text", 29_000, "ok"),
        ("pdf_text", 29_000, "ParserMalformedError:nul"),
        ("pdf_text", 29_000, "ParserMalformedError:note_code"),
        ("pdf_text", 29_000, "ParserMalformedError:failure"),
        ("pdf_text", 29_000, "ParserRefusedError:protected"),
        ("pdf_text", 29_000, "ParserMalformedError:not_json"),
        ("pdf_text", 29_000, "ParserTimeoutError:wall_limit"),
        ("pdf_text", 29_000, "ParserDownError:unreachable"),
    ]
    for record in caplog.records:
        assert "PRIVATE" not in record.getMessage()
        assert "PRIVATE" not in repr(vars(record))
        assert record.exc_info is None
    assert all(type(record.ms) is int for record in records)


def test_an_error_carries_codes_and_integers_and_never_the_parsers_words() -> None:
    async def test() -> None:
        hostile = (
            framed({"ok": False, "failure": "file", "code": "PRIVATE words of a document"}),
            framed({"ok": True, "text": "PRIVATE\x00"}),
            framed({"ok": True, "notes": [{"code": "pages_not_read", "from": "PRIVATE"}]}),
            framed(b"", ok=False, failure="server", code="PRIVATE"),
            framed(b"PRIVATE"),
            b"PRIVATE\n",
        )
        for reply in hostile:
            async with lying(reply) as port:
                error = await error_of(port)
            for shown in (str(error), repr(error), repr(error.args), repr(dict(error.values))):
                assert "PRIVATE" not in shown
            assert error.__cause__ is None or "PRIVATE" not in repr(error.__cause__)

    run(test)


# --- the death of a child that decodes a user's file (review of PR #157, B2 and B3) -------------

DEATHS = [("crashed", "bomb"), ("cpu_limit", "too_slow"), ("wall_limit", "too_slow")]


@pytest.mark.parametrize("kind", ["image", "pdf_render"])
@pytest.mark.parametrize(("code", "becomes"), DEATHS)
def test_the_death_of_an_image_or_render_child_is_the_files_failure(
    kind: str, code: str, becomes: str, caplog: pytest.LogCaptureFixture
) -> None:
    async def test() -> None:
        async with lying(framed(b"", ok=False, failure="server", code=code)) as port:
            with pytest.raises(pc.ParserError) as raised:
                await pc.parse(kind, b"a file", host="127.0.0.1", port=port)
        error = raised.value
        assert type(error) is pc.ParserRefusedError
        assert (error.failure, error.code) == ("file", becomes)
        assert becomes in pc.FILE_CODES

    with caplog.at_level("INFO", logger=pc.logger.name):
        run(test)
    # The log keeps how the child ended, beside what the worker is told.
    outcomes = [record.outcome for record in caplog.records if hasattr(record, "outcome")]
    assert len(outcomes) == 1
    assert outcomes[0].endswith(f":{code}:file/{becomes}")


@pytest.mark.parametrize("kind", ["pdf_text", "docx", "linkedin_zip"])
@pytest.mark.parametrize(("code", "becomes"), DEATHS)
def test_the_death_of_any_other_child_stays_a_failure_of_the_server(
    kind: str, code: str, becomes: str
) -> None:
    async def test() -> None:
        async with lying(framed(b"", ok=False, failure="server", code=code)) as port:
            with pytest.raises(pc.ParserError) as raised:
                await pc.parse(kind, b"a file", host="127.0.0.1", port=port)
        assert (raised.value.failure, raised.value.code) == ("server", code)

    run(test)


@pytest.mark.parametrize("code", sorted(pc.SERVER_CODES - {"crashed", "cpu_limit", "wall_limit"}))
def test_what_is_not_the_death_of_the_child_stays_a_failure_of_the_server(code: str) -> None:
    """Busy, a child that could not lock itself down or start, an answer over the cap: none
    of these says the file killed anything."""

    async def test() -> None:
        async with lying(framed(b"", ok=False, failure="server", code=code)) as port:
            with pytest.raises(pc.ParserError) as raised:
                await pc.parse("image", b"a file", host="127.0.0.1", port=port)
        assert (raised.value.failure, raised.value.code) == ("server", code)

    run(test)
    own = pc.ParserTimeoutError("deadline")  # this client's own clock: the machine, not the file
    assert pc.blame("image", own) is own
    child = pc.ParserRefusedError("memory_limit")  # the child's own word, before any decoding
    assert pc.blame("pdf_render", child) is child
    assert set(pc.FILE_DEATHS) <= pc.SERVER_CODES
    assert set(pc.FILE_DEATHS.values()) <= pc.FILE_CODES
    assert frozenset({"image", "pdf_render"}) == pc.FILE_DEATH_KINDS


def test_through_the_real_server_an_image_child_that_dies_is_the_files_failure() -> None:
    with BackgroundServer(wall_s=0.5, max_answer=100_000) as background:

        async def test() -> None:
            for kind in ("image", "pdf_render"):
                for options, failure, code in (
                    ({"act": "signal", "name": "SIGSEGV"}, "file", "bomb"),
                    ({"act": "signal", "name": "SIGABRT"}, "file", "bomb"),
                    ({"act": "signal", "name": "SIGKILL"}, "file", "bomb"),
                    ({"act": "signal", "name": "SIGXCPU"}, "file", "too_slow"),
                    ({"act": "sleep", "s": 30}, "file", "too_slow"),
                    ({"act": "exit", "status": 70}, "server", "sandbox"),
                    ({"act": "flood", "blocks": 100}, "server", "output_cap"),
                ):
                    with pytest.raises(pc.ParserError) as raised:
                        await pc.parse(
                            kind, b"a file", options, host="127.0.0.1", port=background.port
                        )
                    assert (raised.value.failure, raised.value.code) == (failure, code), options

        run(test)
