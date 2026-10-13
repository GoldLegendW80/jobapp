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


class SecretFilter(logging.Filter):
    """Rewrites a record so that it holds no credential (module docstring). Never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
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
        return True


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


def configure_logging(level: str = "info") -> None:
    """Route the root, uvicorn and sqlalchemy loggers through one JSON handler on stdout.

    Every other logger of the process ends there too (the SDK, httpx, arq, anything that
    propagates), and the handler's filter takes the credentials out of all of them."""
    handler = logging.StreamHandler(sys.stdout)
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
