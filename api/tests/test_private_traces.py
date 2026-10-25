"""What an exception says never reaches a log line (issue #670).

A marker (a random token) is planted where the data of a real error would be: the message, a
note, the arguments, a chained cause or context, a member of a group, the arguments of the log
call, an extra field. Then the test reads what the one handler of `configure_logging` wrote, and
for the api what a real uvicorn wrote on stdout and stderr, and asks where the marker ended up.
`test_the_canary_sees_a_whole_trace` proves the same search finds a marker in the trace the
logging module would have written, so that an empty answer means something."""

import asyncio
import gc
import io
import json
import logging
import secrets
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from arq.jobs import serialize_result
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app import queue
from app.documents import routers as documents_routers
from app.identity.deps import default_deny
from app.logging_config import (
    CAUSE,
    CONTEXT,
    CUT,
    ERRORS_MAX_DEPTH,
    TRACE_MAX_ERRORS,
    JsonFormatter,
    SafeStreamHandler,
    SecretFilter,
    asyncio_message,
    error_name,
    private_trace,
)
from app.main import create_app
from tests.conftest import make_settings
from tests.mail.canary import find, marker
from tests.test_logging_secrets import Lines, lines

__all__ = ["lines"]

UNREACHABLE = "postgresql+psycopg://u:p@127.0.0.1:1/d"
# One path per module whose routes can raise with personal data in the error: the documents
# routes named by the issue, then every other module's prefix, then a path no module owns.
PATHS = (
    "/api/jobs/t670/documents",
    "/api/jobs/t670/documents/write",
    "/api/document-templates",
    "/api/documents/t670",
    "/api/document-generations/t670",
    "/api/ingestion/t670",
    "/api/mail/t670",
    "/api/profile/t670",
    "/api/agents/t670",
    "/api/apply/t670",
    "/api/browser/t670",
    "/api/opportunities/t670",
    "/api/auth/t670",
    "/api/t670",
)


class HostileError(Exception):
    """An exception that holds the marker everywhere an exception can hold something."""

    def __init__(self, planted: str) -> None:
        super().__init__(f"CV text {planted}", {"token": planted})
        self.add_note(f"note {planted}")
        self.value = planted

    def __repr__(self) -> str:
        return f"HostileError({self.value})"


def raise_everything(planted: str) -> None:
    """A cause, a context, a group and a hostile error, each holding the marker."""
    try:
        try:
            raise UnicodeEncodeError("utf-8", f"plain {planted}", 0, 1, "surrogates not allowed")
        except UnicodeEncodeError as inner:
            group = ExceptionGroup(f"group {planted}", [ValueError(planted), HostileError(planted)])
            raise group from inner
    except ExceptionGroup:
        raise HostileError(planted)  # noqa: B904 - the implicit context is what is tested


def caught(planted: str) -> BaseException:
    try:
        raise_everything(planted)
    except HostileError as error:
        return error
    raise AssertionError("raise_everything returned")


# --- the trace itself ---------------------------------------------------------------------------


def test_the_trace_holds_the_classes_and_frames_and_nothing_said() -> None:
    planted = marker()
    trace = private_trace(caught(planted))
    assert planted not in trace
    assert "CV text" not in trace
    assert "note" not in trace
    assert "plain" not in trace
    names = [line.strip() for line in trace.splitlines()]
    assert names.count("UnicodeEncodeError") == 1
    assert names.count("ExceptionGroup") == 1
    assert names.count("ValueError") == 1
    assert names.count(error_name(HostileError)) == 2
    assert error_name(HostileError) == "tests.test_private_traces.HostileError"
    assert CAUSE in trace
    assert CONTEXT in trace
    assert "member 2 of 2:" in trace
    assert f'File "{__file__}"' in trace
    assert "in raise_everything" in trace
    # Oldest first, as Python prints a chain: the cause, then the group, then the last error.
    assert trace.index("UnicodeEncodeError") < trace.index(CAUSE) < trace.index(CONTEXT)
    assert trace.rstrip().endswith(error_name(HostileError))


def test_the_canary_sees_a_whole_trace() -> None:
    planted = marker()
    error = caught(planted)
    whole = logging.Formatter().formatException((type(error), error, error.__traceback__))
    assert [one.marker for one in find([planted], logs=[whole])] == [planted]
    assert find([planted], logs=[private_trace(error)]) == []


def test_a_suppressed_context_is_left_out_as_python_does() -> None:
    try:
        try:
            raise KeyError("first")
        except KeyError:
            raise ValueError("second") from None
    except ValueError as error:
        trace = private_trace(error)
    assert "KeyError" not in trace
    assert CONTEXT not in trace
    assert trace.splitlines()[-1] == "ValueError"


def test_an_error_never_raised_has_no_frames() -> None:
    assert private_trace(RuntimeError("never raised")) == "RuntimeError"


def test_a_cycle_and_a_long_chain_stay_bounded() -> None:
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__context__, second.__context__ = second, first
    assert private_trace(first).count("RuntimeError") == 2
    error: BaseException = ValueError("0")
    for number in range(1, TRACE_MAX_ERRORS * 3):
        later = ValueError(str(number))
        later.__cause__ = error
        error = later
    trace = private_trace(error)
    assert trace.count("ValueError") == TRACE_MAX_ERRORS
    assert trace.count(CUT) == 1


def test_a_group_of_groups_is_walked_and_capped() -> None:
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [OSError("x")]), KeyError("y")])
    trace = private_trace(nested)
    assert [line.strip() for line in trace.splitlines() if "member" not in line] == [
        "ExceptionGroup",
        "ExceptionGroup",
        "OSError",
        "KeyError",
    ]
    wide = ExceptionGroup("wide", [ValueError(str(n)) for n in range(TRACE_MAX_ERRORS * 2)])
    assert private_trace(wide).count(CUT) == 1
    assert private_trace(wide).count("ValueError") == TRACE_MAX_ERRORS - 1


# --- every logger, through the handler ----------------------------------------------------------


@pytest.mark.parametrize("name", ["app.documents", "uvicorn.error", "arq.worker"])
def test_no_logger_writes_what_an_error_says(lines: Lines, name: str) -> None:
    planted = marker()
    error = caught(planted)
    logger = logging.getLogger(name)
    # arq's own failure line: the error is an argument of the message.
    logger.error("%6.2fs ! %s failed, %s: %s", 0.5, "job", "HostileError", error, exc_info=error)
    logger.error("failed: %(error)s", {"error": error})
    logger.warning("extra", extra={"error": error, "nested": {"list": [error]}})
    logger.error("stack", exc_info=(type(error), error, error.__traceback__), stack_info=True)
    written = lines()
    assert len(written) == 4
    assert find([planted], logs=[json.dumps(written)]) == []
    name = "tests.test_private_traces.HostileError"
    assert written[0]["msg"] == f"  0.50s ! job failed, HostileError: {name}"
    assert written[0]["exc"].endswith("\ntests.test_private_traces.HostileError")
    assert written[1]["msg"] == "failed: tests.test_private_traces.HostileError"
    assert written[2]["error"] == "tests.test_private_traces.HostileError"
    assert written[2]["nested"] == {"list": ["tests.test_private_traces.HostileError"]}
    assert "in raise_everything" in written[3]["exc"]


def test_an_exception_as_the_message_is_written_as_its_class(lines: Lines) -> None:
    """`logger.error(exc)`: the logging module writes `str(msg)`, which is what it says."""
    planted = marker()
    error = caught(planted)
    logger = logging.getLogger("app.documents")
    logger.error(error)
    logger.error([error, {"inner": (error,)}])
    logger.error("%s and %s", [error], {error})
    logger.error("%(errors)s", {"errors": [[error]]})
    deep: list[Any] = [error]
    for _ in range(ERRORS_MAX_DEPTH + 2):
        deep = [deep]
    logger.error("%s", deep)
    written = lines()
    assert find([planted], logs=[json.dumps(written)]) == []
    name = "tests.test_private_traces.HostileError"
    assert [line["msg"] for line in written[:4]] == [
        name,
        f"['{name}', {{'inner': ('{name}',)}}]",
        f"['{name}'] and {{'{name}'}}",
        f"[['{name}']]",
    ]
    # The innermost list is past the depth looked into: written as its type name.
    nested = ERRORS_MAX_DEPTH - 1
    assert written[4]["msg"] == "[" * nested + "'list'" + "]" * nested


async def _leave_a_failed_task(planted: str) -> None:
    async def fail() -> None:
        raise HostileError(planted)

    task = asyncio.get_running_loop().create_task(fail(), name=f"task {planted}")
    await asyncio.sleep(0)  # one turn of the loop: the task runs and fails
    assert task.done()
    del task
    gc.collect()  # the task is finalized unretrieved: asyncio logs "never retrieved"


async def _raise_in_a_callback(planted: str, short: str) -> None:
    def callback(value: str) -> None:
        raise HostileError(planted)

    # reprlib cuts a long argument in the callback's repr, so the argument is a short token.
    asyncio.get_running_loop().call_soon(callback, short)
    await asyncio.sleep(0)


def test_a_failed_task_never_retrieved_writes_no_repr(lines: Lines) -> None:
    """asyncio's own handler writes `future: <Task ... exception=HostileError(...)>` into the
    message (the task's name and its exception's repr); none of it reaches the line."""
    planted = marker()
    asyncio.run(_leave_a_failed_task(planted))
    written = [line for line in lines() if line["logger"] == "asyncio" and line["level"] == "error"]
    assert len(written) == 1
    assert written[0]["msg"] == NEVER
    assert written[0]["exc"].endswith("\ntests.test_private_traces.HostileError")
    assert "in fail" in written[0]["exc"]
    assert find([planted], logs=[json.dumps(lines())]) == []


def test_a_callback_that_raises_writes_no_argument(lines: Lines) -> None:
    """`Exception in callback f('<argument>')` and `handle: <Handle ...>`: the first line is cut
    before the repr, the handle line is dropped."""
    planted, short = marker(), secrets.token_hex(6)
    asyncio.run(_raise_in_a_callback(planted, short))
    written = [line for line in lines() if line["logger"] == "asyncio" and line["level"] == "error"]
    assert len(written) == 1
    assert written[0]["msg"] == ("Exception in callback _raise_in_a_callback.<locals>.callback")
    assert "in callback" in written[0]["exc"]
    assert find([planted, short], logs=[json.dumps(lines())]) == []


NEVER = "Task exception was never retrieved"
CALLBACK = "Exception in callback"


@pytest.mark.parametrize(
    ("message", "written"),
    [
        (f"{NEVER}\nfuture: <Task finished exception=E('x')>", NEVER),
        (
            "Future exception was never retrieved\nfuture: <F>",
            "Future exception was never retrieved",
        ),
        (
            "Task was destroyed but it is pending!\ntask: <T>",
            "Task was destroyed but it is pending!",
        ),
        (
            "Fatal read error on socket transport\ntransport: <T>",
            "Fatal read error on socket transport",
        ),
        (f"{CALLBACK} f('x')\nhandle: <Handle f('x')>", f"{CALLBACK} f"),
        (f"{CALLBACK} <TaskStepMethWrapper object>()", CALLBACK),
        (f"{CALLBACK} g.<locals>.<lambda>('x')", f"{CALLBACK} g.<locals>.<lambda>"),
        (f"{CALLBACK} <locals x='y'>", CALLBACK),
        (f"{CALLBACK} <lambdas>", CALLBACK),
        (f"{CALLBACK} functools.partial(<function g>, 'x')", f"{CALLBACK} functools.partial"),
        (
            "Fatal error: protocol.data_received() call failed.",
            "Fatal error: protocol.data_received",
        ),
        (
            "closing of asynchronous generator <async_generator g>",
            "closing of asynchronous generator",
        ),
        ("Executing <Handle f('x')> took 0.200 seconds", "Executing"),
        ("Getting address info host:'x' took 1.0ms: [('x',)]", "Getting address info host"),
        ("<_SelectorSocketTransport fd=7> pauses reading", "asyncio error"),
        ('Unclosed "x" client', "Unclosed"),
        ("Unclosed [x] client", "Unclosed"),
        ("Unclosed {x} client", "Unclosed"),
        ("value=x", "value"),
        ("", "asyncio error"),
        ("\nfuture: <F>", "asyncio error"),
        (
            "Task <Task pending name='x'> has errored out but its parent task <Task y> is "
            "already completed",
            "Task has errored out but its parent task is already completed",
        ),
    ],
)
def test_an_asyncio_message_keeps_its_sentence_and_no_repr(message: str, written: str) -> None:
    assert asyncio_message(message) == written


def test_a_trace_cached_by_an_earlier_handler_is_replaced() -> None:
    """A stdlib formatter caches the whole trace on the record (`exc_text`): the filter
    rewrites it, for its own handler and every handler after it."""
    planted = marker()
    error = caught(planted)
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, None)
    record.exc_info = (type(error), error, error.__traceback__)
    logging.Formatter().format(record)
    assert record.exc_text is not None
    assert planted in record.exc_text
    assert SecretFilter().filter(record) is True
    assert record.exc_text == private_trace(error)
    assert planted not in logging.Formatter().format(record)
    assert planted not in JsonFormatter().format(record)


def test_the_formatter_alone_never_writes_a_whole_trace() -> None:
    planted = marker()
    error = caught(planted)
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, None)
    record.exc_info = (type(error), error, error.__traceback__)
    assert json.loads(JsonFormatter().format(record))["exc"] == private_trace(error)
    record.exc_info = (None, None, None)
    assert "exc" not in json.loads(JsonFormatter().format(record))


# --- what arq keeps in Redis -----------------------------------------------------------------


def test_the_result_kept_of_a_failed_job_is_the_class_of_its_error() -> None:
    """arq keeps the exception of a failed job as its result, for an hour (run_agent,
    run_sweep): through the app's serializer it is its class name, never what it says."""
    planted = marker()
    error = caught(planted)
    args = ("run_agent", ("run",), {}, 1, 0, False, error, 0, 1, "ref", "q", "id")
    raw = serialize_result(*args, serializer=queue.serialize)
    assert raw is not None
    assert planted.encode() not in raw
    kept = queue.deserialize(raw)
    assert (kept["s"], kept["r"]) == (False, "tests.test_private_traces.HostileError")
    assert kept["a"] == ["run"]
    # Anything else JSON cannot hold is still its text.
    assert queue.deserialize(queue.serialize({"x": b"bytes"})) == {"x": "b'bytes'"}


# --- the api under a real uvicorn ---------------------------------------------------------------


@contextmanager
def served(app: FastAPI) -> Iterator[httpx.Client]:
    """A client of the app behind a real uvicorn on a loopback socket, as the image runs it."""
    # Already listening: a request waits in the backlog until the server accepts it.
    listener = socket.create_server(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        port = listener.getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        listener.close()
    assert not thread.is_alive()


def first(app: FastAPI, path: str, endpoint: Any) -> None:
    """Add a GET route ahead of every route of the app, so no module's route answers first."""
    app.add_api_route(path, endpoint, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())


@contextmanager
def written_by_the_app(app: FastAPI) -> Iterator[io.StringIO]:
    # The app's own handler, as configured by create_app: what the container log receives.
    (handler,) = [one for one in logging.getLogger().handlers if isinstance(one, SafeStreamHandler)]
    written = io.StringIO()
    kept = handler.setStream(written)
    try:
        yield written
    finally:
        handler.setStream(kept)


def test_an_unhandled_error_of_any_route_stays_out_of_every_log(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    planted = marker()
    app = create_app(make_settings(UNREACHABLE))
    # Not about sessions (and the database is unreachable): lift the default deny.
    app.dependency_overrides[default_deny] = lambda: None

    def broken() -> None:
        raise_everything(planted)

    for path in PATHS:
        first(app, path, broken)
    caplog.set_level(logging.INFO)
    answers = []
    with written_by_the_app(app) as written, served(app) as client:
        for number, path in enumerate(PATHS):
            answers.append(client.get(path, headers={"X-Request-ID": f"t670-{number}"}))
    output = capfd.readouterr()
    for number, answer in enumerate(answers):
        assert answer.status_code == 500, PATHS[number]
        assert answer.json() == {"detail": "internal server error"}
        assert answer.headers["x-request-id"] == f"t670-{number}"
    assert answers[PATHS.index("/api/ingestion/t670")].headers["cache-control"] == "no-store"
    assert answers[PATHS.index("/api/mail/t670")].headers["cache-control"] == "no-store"
    logs = [*caplog.records, *written.getvalue().splitlines(), output.out, output.err]
    values = [[answer.text, dict(answer.headers)] for answer in answers]
    assert find([planted], logs=logs, values=values) == []
    # Nothing left the app: uvicorn logged no error of its own.
    assert [one for one in caplog.records if one.name.startswith("uvicorn") and one.exc_info] == []
    lines = [json.loads(line) for line in written.getvalue().splitlines()]
    # The documents routes answer their own errors first (#620, documents_routers.no_store),
    # logged by class only; every other route's error reaches the net of request_log.
    documents = [path for path in PATHS if documents_routers.private(path)]
    assert documents == ["/api/documents/t670", "/api/document-generations/t670"]
    for path in documents:
        assert answers[PATHS.index(path)].headers["cache-control"] == "private, no-store"
    caught_there = [line for line in lines if line["msg"] == "documents.request.failed"]
    assert [line["error"] for line in caught_there] == ["HostileError"] * len(documents)
    failed = [line for line in lines if line["msg"] == "unhandled error"]
    assert [(line["request_id"], line["path"]) for line in failed] == [
        (f"t670-{number}", path) for number, path in enumerate(PATHS) if path not in documents
    ]
    for line in failed:
        assert line["level"] == "error"
        assert line["error"] == "tests.test_private_traces.HostileError"
        assert "in raise_everything" in line["exc"]
        assert line["exc"].endswith("\ntests.test_private_traces.HostileError")
    requests = [line for line in lines if line["msg"] == "request"]
    assert [(line["request_id"], line["status"]) for line in requests] == [
        (f"t670-{number}", 500) for number in range(len(PATHS))
    ]


def test_an_error_after_the_answer_started_is_logged_privately(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Once the first bytes are sent no 500 can be answered: the error leaves request_log, the
    app's net logs it, and uvicorn logs it again, both through the app's handler, so both with
    the private trace."""
    planted = marker()
    app = create_app(make_settings(UNREACHABLE))
    app.dependency_overrides[default_deny] = lambda: None

    def chunks() -> Iterator[bytes]:
        yield b"start"
        raise_everything(planted)

    def streamed() -> StreamingResponse:
        return StreamingResponse(chunks())

    first(app, "/api/jobs/t670/documents/stream", streamed)
    with written_by_the_app(app) as written, served(app) as client:
        answer = client.get("/api/jobs/t670/documents/stream")
    output = capfd.readouterr()
    assert answer.content == b"start"
    logs = [written.getvalue(), output.out, output.err]
    assert find([planted], logs=logs, values=[answer.text, dict(answer.headers)]) == []
    lines = [json.loads(line) for line in written.getvalue().splitlines()]
    errors = [(line["logger"], line["msg"]) for line in lines if "exc" in line]
    assert errors == [
        ("app", "unhandled error"),
        ("uvicorn.error", "Exception in ASGI application\n"),
    ]
    for line in lines:
        if "exc" in line:
            assert "in chunks" in line["exc"]
            assert line["exc"].endswith("\ntests.test_private_traces.HostileError")
