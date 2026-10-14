"""Logging of the mail module: by type and ids only (mail M1).

Nothing of a mail (a subject, an address, a snippet, a body, a search term) and no token ever
reaches a log line. Three things enforce it:

- `event(name, **fields)` is the only way the module logs. A field name outside `FIELDS` is
  refused, and so is a value that is not an integer, a boolean or a short identifier (letters,
  digits and `_.:-`): an address has an `@`, a subject has a space, neither passes.
- `hide(*secrets)` registers tokens with the process wide scrubber (`app.logging_config.hidden`)
  while a call uses them, so a library that logs a header or quotes a request cannot leak one.
- `quiet()` silences httpx's own request line (`HTTP Request: GET <address> ...`) for the calls
  made inside it: the address of `messages.list` carries the search terms. The filter sits on
  the `httpx` logger for the whole process and does nothing outside `quiet()`. (httpcore logs
  hosts and header names at DEBUG, never an address or a value of ours.)
"""

from __future__ import annotations

import contextvars
import logging
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from urllib.parse import quote, quote_plus

from app.logging_config import HIDDEN_MIN, hidden

logger = logging.getLogger("app.mail")

REFUSED = "[refused]"
# Every field a mail log line may carry. Ids and counts, never content.
FIELDS = frozenset(
    {
        "user_id",
        "call",
        "status",
        "code",
        "reason",
        "attempt",
        "retry_after",
        "units",
        "bytes",
        "count",
        "pages",
        "limit",
        "message_id",
        "thread_id",
        "history_id",
        "gone",
        "truncated",
        "waiting",
        "seconds",
    }
)
_IDENTIFIER = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")
_NAME = re.compile(r"\A[a-z][a-z0-9_.]{2,63}\Z")

_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("mail_quiet_http", default=False)


def safe(value: object) -> object:
    """`value` when it is an id or a count, else `REFUSED`."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str) and _IDENTIFIER.match(value):
        return value
    return REFUSED


def event(name: str, *, level: int = logging.INFO, **fields: object) -> None:
    """One log line: `name` (a fixed dotted word) and fields of `FIELDS` only."""
    if not _NAME.match(name):
        raise ValueError("a mail log event is named by a fixed dotted word")
    unknown = sorted(set(fields) - FIELDS)
    if unknown:
        raise ValueError(f"not a mail log field: {', '.join(unknown)}")
    logger.log(level, name, extra={key: safe(value) for key, value in fields.items()})


@contextmanager
def hide(*secrets: str | None) -> Iterator[None]:
    """While open, each secret is replaced in every log record of the process. A value shorter
    than the scrubber accepts (no real token is) is skipped."""
    with ExitStack() as stack:
        for secret in secrets:
            if not secret or len(secret) < HIDDEN_MIN:
                continue
            # Also as a form or an address carries it: a refresh token of Google starts with
            # `1//`, which travels as `1%2F%2F`.
            for shape in {secret, quote(secret, safe=""), quote_plus(secret)}:
                stack.enter_context(hidden(shape))
        yield


class _QuietHttp(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (_quiet.get() and record.levelno < logging.WARNING)


logging.getLogger("httpx").addFilter(_QuietHttp())


@contextmanager
def quiet() -> Iterator[None]:
    """While open, in this task, the HTTP libraries log nothing under WARNING."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)
