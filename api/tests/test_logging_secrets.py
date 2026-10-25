"""No model credential in a log line, whoever logs (issue #90, point 1).

The app's own code never logs a credential; the libraries it runs log on their own loggers, and
the one handler of `configure_logging` is where all of them end. Every test here logs through a
third party logger's name with made up credentials of the right shape (never a real one), and
reads what the handler wrote."""

import base64
import io
import json
import logging
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app import logging_config
from app.logging_config import REDACTED, JsonFormatter, SecretFilter, configure_logging, hidden

PREFIX = "sk-ant-"
API_KEY = "sk-ant-api03-" + "Lg4_" * 20 + "Wxyz"
OAUTH = "sk-ant-oat01-" + "Lo9_" * 20 + "Tok3"
# The shortest and the longest credential the app stores (credentials.MIN_LENGTH, MAX_LENGTH).
SHORTEST = "sk-ant-api03-" + "s7Q" * 9
LONGEST = "sk-ant-api03-" + "Zq8_" * 96 + "End"
# A secret of no known shape: only hidden while `hidden` is open.
ODD = "tok." + "Qz7x" * 9
SHAPED = {"api key": API_KEY, "subscription token": OAUTH, "shortest": SHORTEST, "longest": LONGEST}
EVERY = {**SHAPED, "no shape": ODD}
# The loggers of what the worker and the api run: the SDK, the HTTP clients, the queue, the
# server, the database driver, the event loop.
THIRD_PARTY = (
    "claude_agent_sdk._internal.query",
    "anthropic._base_client",
    "httpx",
    "httpcore.http11",
    "arq.worker",
    "uvicorn.error",
    "sqlalchemy.engine.Engine",
    "asyncio",
)

Lines = Callable[[], list[dict[str, Any]]]


def test_the_made_up_credentials_have_the_lengths_the_app_stores() -> None:
    from app.agents import credentials

    assert len(SHORTEST) == credentials.MIN_LENGTH
    assert len(LONGEST) == credentials.MAX_LENGTH


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> Iterator[Lines]:
    """The process's logging as the worker and the api set it up, written to a buffer."""
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    uvicorn = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    access_disabled = logging.getLogger("uvicorn.access").disabled
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    configure_logging("debug")

    def read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in out.getvalue().splitlines()]

    try:
        yield read
    finally:
        root.handlers, root.level = before[0], before[1]
        for name, (handlers, propagate) in uvicorn.items():
            logging.getLogger(name).handlers = handlers
            logging.getLogger(name).propagate = propagate
        logging.getLogger("uvicorn.access").disabled = access_disabled


class Request:
    """An object that only shows the secret as text, like a request or a URL of a library."""

    def __init__(self, secret: str) -> None:
        self.secret = secret

    def __str__(self) -> str:
        return f"<Request authorization={self.secret}>"


def say_everything(name: str, secret: str) -> None:
    """Every place a record can hold a text: the message, its arguments, the extra fields at
    any depth (names and values), the exception and the stack."""
    logger = logging.getLogger(name)
    logger.error(f"Fatal error in message reader: {secret}")
    logger.error("header %s then %r and %s", secret, {"x-api-key": secret}, Request(secret))
    logger.warning(
        "extra fields",
        extra={
            "token": secret,
            "nested": {"list": [secret, ("tuple", secret)], secret: "as a name"},
            "request": Request(secret),
            "listed": [Request(secret)],
        },
    )
    try:
        raise ValueError(f"cannot decode {secret}")
    except ValueError:
        logger.exception("failed with %s", secret, stack_info=True)


def redactions(name: str) -> int:
    """How many `REDACTED` the lines of `say_everything` hold at least. A message of asyncio is
    written up to its first repr only (issue #670), and "[redacted]" opens like one: the five
    of the messages go with what is cut, the extra fields and the stack keep theirs."""
    return 6 if name == "asyncio" else 11


@pytest.mark.parametrize("name", THIRD_PARTY)
@pytest.mark.parametrize("secret", list(EVERY.values()), ids=list(EVERY))
def test_a_credential_in_use_never_reaches_a_line_of_any_logger(
    lines: Lines, name: str, secret: str
) -> None:
    with hidden(secret):
        say_everything(name, secret)
    written = lines()
    assert len(written) == 4
    whole = json.dumps(written)
    assert secret not in whole
    assert secret[:24] not in whole  # not even its beginning
    assert whole.count(REDACTED) >= redactions(name)
    assert written[0] == {**written[0], "logger": name, "level": "error"}
    if name == "asyncio":  # cut before its first repr, "[redacted]" included (issue #670)
        assert written[0]["msg"] == "Fatal error in message reader"
    else:
        assert written[0]["msg"] == f"Fatal error in message reader: {REDACTED}"
    # What the error says is never written at all (issue #670): the trace ends at its class.
    assert written[3]["exc"].endswith(", in say_everything\nValueError")


@pytest.mark.parametrize("name", THIRD_PARTY)
@pytest.mark.parametrize("secret", list(SHAPED.values()), ids=list(SHAPED))
def test_anything_shaped_like_a_credential_is_replaced_without_being_in_use(
    lines: Lines, name: str, secret: str
) -> None:
    """The credential of a request that is being saved, or of another process: nothing was
    told about it, and it is replaced by its shape alone."""
    say_everything(name, secret)
    whole = json.dumps(lines())
    assert secret not in whole
    assert PREFIX not in whole
    assert whole.count(REDACTED) >= redactions(name)


@pytest.mark.parametrize("secret", list(SHAPED.values()), ids=list(SHAPED))
def test_a_credential_cut_by_the_library_before_it_was_logged(lines: Lines, secret: str) -> None:
    """The SDK quotes the first hundred characters of a raw line of the CLI: what it logs is
    the beginning of the credential, which no longer equals it. Every cut that keeps the
    prefix is replaced whole, hidden or not: nothing of the random part is written."""
    logger = logging.getLogger("claude_agent_sdk._internal.query")
    cuts = range(len(PREFIX), len(secret) + 1)
    with hidden(secret):
        for cut in cuts:
            logger.error("Fatal error in message reader: JSON decode error: %s", secret[:cut])
    for cut in cuts:
        logger.error(f'JSON decode error: {{"env":{{"KEY":"{secret[:cut]}')
    written = lines()
    assert len(written) == 2 * len(cuts)
    for line in written[: len(cuts)]:
        assert line["msg"] == f"Fatal error in message reader: JSON decode error: {REDACTED}"
    for line in written[len(cuts) :]:
        assert line["msg"] == f'JSON decode error: {{"env":{{"KEY":"{REDACTED}'


def test_the_hundred_first_characters_of_a_line_that_holds_the_credential(lines: Lines) -> None:
    """The very message of the issue: `Fatal error in message reader: {e}` where the error
    quotes `line[:100]`, at every position of the credential in the line."""
    logger = logging.getLogger("claude_agent_sdk._internal.query")
    for lead in range(0, 100 - len(PREFIX)):
        line = "x" * lead + API_KEY + '","more":"' + "y" * 200
        with hidden(API_KEY):
            logger.error(f"Fatal error in message reader: Failed to decode JSON: {line[:100]}...")
    for lead, line in enumerate(lines()):
        assert PREFIX not in line["msg"], lead
        assert "Lg4_" not in line["msg"], lead
        assert REDACTED in line["msg"], lead


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# What the module docstring names as out of reach (issue #155, point 3): (what a library logs,
# what of it stays written).
OUT_OF_REACH = {
    "upper case": (API_KEY.upper(), API_KEY.upper()),
    "whitespace inside the key": (API_KEY[:30] + " " + API_KEY[30:], API_KEY[30:]),
    "the key without its head": (API_KEY[len(PREFIX) :], API_KEY[len(PREFIX) :]),
    "base64 of the key": (b64(API_KEY), b64(API_KEY)),
    "base64 of user:key": ("Basic " + b64(f"user:{API_KEY}"), b64(f"user:{API_KEY}")),
    "hex": (API_KEY.encode().hex(), API_KEY.encode().hex()),
    "another dash": (API_KEY.replace("-", "\u2010"), API_KEY.replace("-", "\u2010")),
}


@pytest.mark.parametrize(("logged", "stays"), list(OUT_OF_REACH.values()), ids=list(OUT_OF_REACH))
def test_the_encodings_the_module_docstring_names_stay_out_of_reach(
    lines: Lines, logged: str, stays: str
) -> None:
    """The documented limit, pinned: the credential in use and its shape on, each of these is
    written as it is. A matcher for one of them changes this test and that line together."""
    with hidden(API_KEY):
        logging.getLogger("httpx").error("header %s", logged)
    [line] = lines()
    assert stays in line["msg"]


def test_a_secret_of_no_known_shape_is_hidden_only_while_it_is_in_use(lines: Lines) -> None:
    logger = logging.getLogger("httpx")
    with hidden(ODD):
        with hidden(ODD):  # two runs of one user at once
            logger.error("first %s", ODD)
        logger.error("second %s", ODD)  # one of the two ended: still in use
    logger.error("third %s", ODD)
    assert [line["msg"] for line in lines()] == [
        f"first {REDACTED}",
        f"second {REDACTED}",
        f"third {ODD}",
    ]


def test_of_two_secrets_in_use_the_longer_is_replaced_first(lines: Lines) -> None:
    longer = ODD + ".and.more"
    with hidden(ODD), hidden(longer):
        logging.getLogger("arq.worker").error("job failed: %s", longer)
    assert lines()[0]["msg"] == f"job failed: {REDACTED}"


def test_a_short_secret_is_refused_instead_of_mangling_every_line() -> None:
    for short in ("", "a", "sk-ant-", "x" * (logging_config.HIDDEN_MIN - 1)):
        with pytest.raises(ValueError, match="at least"), hidden(short):
            pass
    assert logging_config.clean_text("a banana") == "a banana"


def test_the_record_itself_is_rewritten_for_whatever_handles_it_next() -> None:
    """The filter does not only change what its own handler writes: the message, the extra
    fields and the text of the exception that the record holds are clean, so a handler of
    the logging module that comes next writes no credential either."""
    try:
        raise RuntimeError(API_KEY)
    except RuntimeError:
        caught = sys.exc_info()
    record = logging.LogRecord("httpx", logging.ERROR, __file__, 1, "sent %s", (API_KEY,), caught)
    record.headers = {"authorization": f"Bearer {API_KEY}"}
    assert SecretFilter().filter(record) is True
    assert record.getMessage() == f"sent {REDACTED}"
    assert record.args is None
    assert record.exc_info is caught  # kept, for whoever reads the record in code
    assert record.exc_text is not None
    assert record.exc_text.endswith(
        ", in test_the_record_itself_is_rewritten_for_whatever_handles_it_next\nRuntimeError"
    )
    assert record.headers == {"authorization": f"Bearer {REDACTED}"}
    plain = logging.Formatter("%(name)s %(message)s %(headers)s").format(record)
    assert API_KEY not in plain
    assert plain.count(REDACTED) == 2
    assert json.loads(JsonFormatter().format(record))["exc"] == record.exc_text


def test_a_message_whose_arguments_do_not_fit_is_still_written_clean(lines: Lines) -> None:
    logging.getLogger("httpx").error("%d and %d", API_KEY)
    assert API_KEY not in json.dumps(lines())
    assert REDACTED in lines()[0]["msg"]


def test_the_formatter_cleans_the_finished_line_by_itself() -> None:
    """The last look, for a handler that was given the formatter without the filter."""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key %s", (API_KEY,), None)
    record.request = Request(OAUTH)
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == f"key {REDACTED}"
    assert line["request"] == f"<Request authorization={REDACTED}>"


def test_a_line_without_a_credential_is_written_as_it_was(lines: Lines) -> None:
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    logger.error("SELECT %s FROM jobs WHERE id = %s", "title", 7, extra={"rows": 3, "ok": True})
    logger.error("Paste a regular API key, which starts with sk-ant-api")
    first, second = lines()
    assert first["msg"] == "SELECT title FROM jobs WHERE id = 7"
    assert (first["rows"], first["ok"]) == (3, True)
    assert second["msg"] == f"Paste a regular API key, which starts with {REDACTED}"


# A record that cannot be written, a filter that fails: the log call never raises, and nothing
# of the credential is written, to stdout or to stderr (review of PR #153, B1 and N1).
FAKE_KEY = "sk-ant-api03-" + "Pq3_" * 22 + "Zz9"


@pytest.fixture
def real_handler() -> Iterator[Callable[[], None]]:
    """Call it inside the test, once `capfd` is capturing: `configure_logging` on the real
    stdout, restored when the test ends. Read with `capfd`."""
    root = logging.getLogger()
    before = (list(root.handlers), root.level)

    yield lambda: configure_logging("info")
    root.handlers, root.level = before[0], before[1]


def pieces(secret: str, size: int = 16) -> list[str]:
    return [secret[i : i + size] for i in range(len(secret) - size + 1)]


def assert_none_of_it(text: str, secret: str) -> None:
    assert secret not in text
    assert not [piece for piece in pieces(secret) if piece in text]


@pytest.mark.parametrize("registered", [False, True], ids=["by shape", "hidden"])
def test_a_record_that_cannot_be_written_leaves_the_credential_nowhere(
    real_handler: Callable[[], None], capfd: pytest.CaptureFixture[str], registered: bool
) -> None:
    import contextlib
    import uuid

    real_handler()
    logger = logging.getLogger("app.agents.runner")
    with hidden(FAKE_KEY) if registered else contextlib.nullcontext():
        try:
            raise RuntimeError(f"spawn failed with ANTHROPIC_API_KEY={FAKE_KEY}")
        except RuntimeError:
            logger.warning("agent run error", extra={"m": {uuid.uuid4(): 1}})
    out, err = capfd.readouterr()
    for stream in (out, err):
        assert_none_of_it(stream, FAKE_KEY)
    written = [json.loads(line) for line in (out + err).splitlines() if line.strip()]
    assert len(written) == 1
    assert written[0]["msg"] == logging_config.LOST
    assert written[0]["logger"] == "app.agents.runner"
    assert written[0]["level"] == "warning"
    assert written[0]["error"] == "TypeError"
    assert set(written[0]) == {"ts", "level", "logger", "msg", "error"}


def test_a_stream_that_fails_to_write_gives_no_traceback_and_no_exception(
    real_handler: Callable[[], None], capfd: pytest.CaptureFixture[str]
) -> None:
    real_handler()
    handler = logging.getLogger().handlers[0]

    def broken(self: object, text: str) -> int:
        raise BrokenPipeError(f"cannot write {FAKE_KEY}")

    class Closed(io.StringIO):
        write = broken  # type: ignore[assignment]

    handler.stream = Closed()  # type: ignore[attr-defined]
    try:
        raise RuntimeError(f"spawn failed with {FAKE_KEY}")
    except RuntimeError:
        logging.getLogger("x").error("lost")  # must not raise
    out, err = capfd.readouterr()
    assert_none_of_it(out + err, FAKE_KEY)


def _cyclic() -> dict[str, Any]:
    loop: list[Any] = []
    loop.append(loop)
    return {"m": loop}


class Unprintable:
    def __str__(self) -> str:
        raise RuntimeError(f"cannot print {FAKE_KEY}")


@pytest.mark.parametrize(
    "shape",
    ["tuple key", "frozenset key", "cyclic extra", "message whose str raises"],
)
def test_an_error_inside_the_filter_never_reaches_the_caller_nor_lets_the_record_through(
    real_handler: Callable[[], None], capfd: pytest.CaptureFixture[str], shape: str
) -> None:
    real_handler()
    logger = logging.getLogger("app.agents.runner")
    extra: dict[str, Any] = {"token": FAKE_KEY}
    message: Any = f"agent run error {FAKE_KEY}"
    if shape == "tuple key":
        extra["m"] = {("a", "b"): FAKE_KEY}
    elif shape == "frozenset key":
        extra["m"] = {frozenset({"a", FAKE_KEY}): 1}
    elif shape == "cyclic extra":
        extra.update(_cyclic())
    else:
        message = Unprintable()
    try:
        raise RuntimeError(f"spawn failed with {FAKE_KEY}")
    except RuntimeError:
        logger.warning(message, extra=extra, exc_info=True, stack_info=True)  # must not raise
    out, err = capfd.readouterr()
    assert_none_of_it(out + err, FAKE_KEY)
    (line,) = [json.loads(text) for text in out.splitlines()]
    assert err == ""
    assert line["msg"] == logging_config.LOST
    assert set(line) == {"ts", "level", "logger", "msg", "error"}
