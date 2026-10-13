"""Structured logging: one JSON object per line on stdout.

Model credentials never reach a log line, whoever logs (issue #90). The app's own code never logs
one; the libraries it runs are not held to that (the Claude Agent SDK quotes the beginning of a
raw line of the CLI when it cannot read it, an HTTP client may log a header). So the one handler
every logger ends in has a filter, `SecretFilter`, that rewrites each record before it is
written: the message (formatted with its arguments), every extra field at any depth, and the
text of the exception and of the stack. It replaces two things:

- the credentials in use right now, character for character (`hidden(secret)`: the runner holds
  it open for the whole run, so the credential of a run is known here while it is decrypted);
- anything shaped like an Anthropic credential, `SHAPE`, in use or not: the prefix and every
  character a credential is made of that follows it. This is what catches a credential a
  library cut before logging it (the first hundred characters of a line), which no longer
  equals the credential, and the credential of a request that is being saved. Nothing of the
  random part is written as long as the prefix was. The price: a line that only names the
  prefix loses it too.

The formatter looks at the finished line once more, for a value the filter could not walk (an
object that only shows its text when it is encoded). What stays out of reach is what `scrub` of
app/agents/runner.py names too: a credential cut somewhere else than at its end, or rewritten.
"""

import json
import logging
import re
import sys
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

REDACTED = "[redacted]"
# A shorter secret is refused by `hidden`: replacing a few characters everywhere would mangle
# every line and hide nothing. Far under the shortest credential the app stores (40).
HIDDEN_MIN = 16
SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]*")

_lock = threading.Lock()
_counts: dict[str, int] = {}
# What `clean` reads: replaced as a whole under the lock, never changed in place, longest first
# (of two credentials where one is the beginning of the other, the longer goes first).
_hidden: tuple[str, ...] = ()


@contextmanager
def hidden(secret: str) -> Iterator[None]:
    """While this is open, `secret` is replaced in every log record of the process. May be
    open several times at once for the same value (two runs of one user)."""
    global _hidden
    if len(secret) < HIDDEN_MIN:
        raise ValueError(f"a secret to hide has at least {HIDDEN_MIN} characters")
    with _lock:
        _counts[secret] = _counts.get(secret, 0) + 1
        _hidden = tuple(sorted(_counts, key=len, reverse=True))
    try:
        yield
    finally:
        with _lock:
            _counts[secret] -= 1
            if not _counts[secret]:
                del _counts[secret]
            _hidden = tuple(sorted(_counts, key=len, reverse=True))


def clean_text(text: str) -> str:
    """`text` without the credentials in use and without anything shaped like one."""
    for secret in _hidden:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return SHAPE.sub(REDACTED, text) if "sk-ant-" in text else text


def clean(value: Any) -> Any:
    """`value` with every text in it cleaned: strings, the items of lists, tuples and sets,
    the names and the values of mappings. Anything else is kept when its text is clean and
    replaced by its cleaned text when it is not (an exception, a URL, a request object)."""
    if isinstance(value, str):
        return clean_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [clean(item) for item in value]
    try:
        text = str(value)
    except Exception:
        return "[unprintable]"
    cleaned = clean_text(text)
    return value if cleaned == text else cleaned


LOST = "a log record could not be written"


def _lost_line(record: logging.LogRecord, error: BaseException | type | None) -> str:
    """The one line that stands for a record that could not be written: when, the level, the
    logger's name (scrubbed), the fixed text, the TYPE of the error. Nothing of the record."""
    kind = error if isinstance(error, type) else type(error)
    payload = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": clean_text(str(getattr(record, "levelname", "")).lower()),
        "logger": clean_text(str(getattr(record, "name", ""))),
        "msg": LOST,
        "error": clean_text(kind.__name__) if error is not None else "unknown",
    }
    return clean_text(json.dumps(payload, ensure_ascii=False))


class SecretFilter(logging.Filter):
    """Rewrites a record so that it holds no credential (module docstring). Never drops one,
    never raises into the code that logged: a record the filter cannot walk becomes the fixed
    line of `LOST`, with no arguments, no exception, no stack and no extra field."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._scrub(record)
        except Exception as error:
            self._replace(record, error)
        return True

    @staticmethod
    def _replace(record: logging.LogRecord, error: BaseException) -> None:
        try:
            for key in list(vars(record)):
                if key not in _RESERVED and not key.startswith("_"):
                    delattr(record, key)
            record.name = clean_text(str(record.name))
            record.msg, record.args = LOST, None
            record.exc_info = record.exc_text = record.stack_info = None
            record.__dict__["error"] = type(error).__name__
        except Exception:
            # Cannot be reached with a real record; a hostile one gets the bare minimum.
            record.__dict__.clear()
            record.__dict__.update(name="", msg=LOST, args=None, levelname="WARNING", levelno=30)

    @staticmethod
    def _scrub(record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            # Arguments that do not fit the message: the handler would fail on it later.
            message = f"{record.msg!s} {record.args!r}"
        # The arguments are folded into the message: an argument can be any object, and only
        # its text in the message is what would be written.
        record.msg, record.args = clean_text(message), None
        for key, value in list(vars(record).items()):
            if key not in _RESERVED and not key.startswith("_"):
                setattr(record, key, clean(value))
        if record.exc_info and not record.exc_text:
            # Where the logging module keeps the text of a record's exception once it has
            # been formatted: its own formatters write this text and never format the
            # exception again, and so does JsonFormatter. The exception object stays on the
            # record for whoever reads it in code (it cannot be rewritten).
            record.exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        if record.exc_text:
            record.exc_text = clean_text(record.exc_text)
        if record.stack_info:
            record.stack_info = clean_text(record.stack_info)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text  # formatted already, and cleaned, by SecretFilter
        elif record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Once more on the finished line: a credential is made of characters JSON never
        # escapes, so it stands in the line as it stood in the value.
        return clean_text(json.dumps(payload, default=str, ensure_ascii=False))


class SafeStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """The one handler of the app. A record that cannot be written (a value the JSON encoder
    refuses, a closed stream) is reported by `Handler.handleError` with the record and the
    traceback, which quote whatever the exception being handled quoted, credential included,
    past the filter and the formatter. Here it is one fixed line (`LOST` and the type of the
    error), and never an exception."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - the stdlib name
        try:
            line = _lost_line(record, sys.exc_info()[0])
            self.stream.write(line + self.terminator)
            self.flush()
        except Exception:  # noqa: S110 - a logger that cannot report must stay silent
            pass


def configure_logging(level: str = "info") -> None:
    """Route the root, uvicorn and sqlalchemy loggers through one JSON handler on stdout.

    Every other logger of the process ends there too (the SDK, httpx, arq, anything that
    propagates), and the handler's filter takes the credentials out of all of them."""
    handler = SafeStreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # The access log is replaced by the request middleware of app.main.
    logging.getLogger("uvicorn.access").disabled = True
