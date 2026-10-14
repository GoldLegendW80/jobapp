"""The canary of the mail module's tests (mail M1). Two jobs.

**No test reaches Google.** `block_google()` replaces the name lookups of the `socket` module
for the whole test session: asking for a host of Google (`googleapis.com`, `google.com` and
everything under them, so `oauth2.googleapis.com`, `gmail.googleapis.com`,
`accounts.google.com`) raises `RealGoogleReachedError` and is written down in `reached`. The
fixture `no_real_google` of conftest.py installs it and fails the test that caused an entry,
even when the code under test swallowed the exception. Every other host is looked up as before.
The second lock is in the client itself: `app.mail.google.endpoints` refuses a Google address
under APP_ENV=test.

**Nothing of a mail or a token is left behind.** A test plants a marker (`marker()`: a random
token, never a common word) in a token, a subject or a body, runs the code, then asks
`find(markers, logs=..., redis=..., values=...)` where the marker ended up. An empty answer
means something only because `test_canary.py` proves that a marker planted in a log line and in
a Redis value IS found. Every later task runs `find` over the logs, the Redis keys and the rows
its code can write.
"""

from __future__ import annotations

import logging
import secrets
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

GOOGLE_SUFFIXES = ("googleapis.com", "google.com")
_LOOKUPS = ("getaddrinfo", "gethostbyname", "gethostbyname_ex")

# Every Google host a test process asked for, since `block_google()`.
reached: list[str] = []


class RealGoogleReachedError(AssertionError):
    """A test process tried to resolve a real Google host."""


def is_google(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in GOOGLE_SUFFIXES)


def block_google() -> Callable[[], None]:
    """Install the block. The function returned puts the lookups back as they were."""
    originals = {name: getattr(socket, name) for name in _LOOKUPS}

    def guard(name: str) -> Callable[..., Any]:
        original = originals[name]

        def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
            if is_google(host):
                host = host.decode("ascii", "replace") if isinstance(host, bytes) else host
                reached.append(str(host).lower().rstrip("."))
                raise RealGoogleReachedError(
                    f"a test tried to reach the real Google host {host!r}: tests use the fake "
                    "of api/tests/mail/fake_gmail.py"
                )
            return original(host, *args, **kwargs)

        return lookup

    for name in _LOOKUPS:
        setattr(socket, name, guard(name))

    def restore() -> None:
        for name, original in originals.items():
            setattr(socket, name, original)

    return restore


# --- markers --------------------------------------------------------------------------------


def marker(label: str = "canary") -> str:
    """A random token to plant: long enough for the log scrubber, and found nowhere by chance."""
    return f"{label}-{secrets.token_hex(16)}"


@dataclass(frozen=True)
class Finding:
    where: str  # "log", "redis key", "redis value", "value"
    place: str  # the logger, the key, or the position in `values`
    marker: str


def _text_of(value: Any) -> str:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, dict):
        return " ".join(_text_of(key) + " " + _text_of(item) for key, item in value.items())
    if isinstance(value, list | tuple | set | frozenset):
        return " ".join(_text_of(item) for item in value)
    return str(value)


def record_text(record: logging.LogRecord) -> str:
    """Everything a handler could write of a record: the message with its arguments, every
    extra field, the exception and the stack."""
    parts = [record.name]
    try:
        parts.append(record.getMessage())
    except Exception:
        parts.append(_text_of(record.msg) + " " + _text_of(record.args))
    parts.append(_text_of({key: value for key, value in vars(record).items() if key != "msg"}))
    if record.exc_info:
        parts.append(logging.Formatter().formatException(record.exc_info))
    return " ".join(parts)


def _redis_values(client: Any, key: bytes) -> Any:
    kind = client.type(key)
    kind = kind.decode() if isinstance(kind, bytes) else kind
    if kind == "string":
        return client.get(key)
    if kind == "list":
        return client.lrange(key, 0, -1)
    if kind == "hash":
        return client.hgetall(key)
    if kind == "set":
        return list(client.smembers(key))
    if kind == "zset":
        return client.zrange(key, 0, -1)
    if kind == "stream":
        return client.xrange(key)
    return b""


def find(
    markers: Iterable[str],
    *,
    logs: Iterable[logging.LogRecord | str] = (),
    redis: Any = None,
    redis_match: str = "*",
    values: Iterable[Any] = (),
) -> list[Finding]:
    """Where the markers are. `logs` takes records (caplog.records) or finished lines; `redis`
    a synchronous client, whose keys under `redis_match` are read name and value, whatever
    their type (an arq result is a pickle: a text inside it is still its bytes); `values` is
    anything else to look through: rows, job arguments, an exception's text."""
    wanted = [item for item in markers if item]
    found: list[Finding] = []

    def look(where: str, place: str, text: str) -> None:
        found.extend(Finding(where, place, item) for item in wanted if item in text)

    for entry in logs:
        if isinstance(entry, logging.LogRecord):
            look("log", entry.name, record_text(entry))
        else:
            look("log", "line", str(entry))
    if redis is not None:
        for key in redis.scan_iter(match=redis_match, count=500):
            name = _text_of(key)
            look("redis key", name, name)
            look("redis value", name, _text_of(_redis_values(redis, key)))
    for position, value in enumerate(values):
        look("value", str(position), _text_of(value))
    return found
