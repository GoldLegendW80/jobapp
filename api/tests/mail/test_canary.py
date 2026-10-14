"""The canary itself (mail M1): it blocks Google at the socket, and it finds what is planted,
so that a later "nothing found" means something."""

from __future__ import annotations

import asyncio
import logging
import pickle
import socket
import uuid

import httpx
import pytest
import redis as redis_client

from app.logging_config import JsonFormatter
from tests.mail import canary
from tests.mail.conftest import run


def _attempt(lookup: object, *arguments: object) -> None:
    """Ask for a Google host and take the entry out of the list, so `no_real_google` passes:
    this file is the one place that asks on purpose."""
    before = len(canary.reached)
    with pytest.raises(canary.RealGoogleReachedError, match="real Google host"):
        lookup(*arguments)  # type: ignore[operator]
    assert len(canary.reached) == before + 1
    del canary.reached[before:]


@pytest.mark.parametrize(
    "host",
    [
        "googleapis.com",
        "gmail.googleapis.com",
        "oauth2.googleapis.com",
        "openidconnect.googleapis.com",
        "accounts.google.com",
        "www.google.com",
        "GMAIL.GoogleAPIs.com.",
    ],
)
def test_resolving_a_real_google_host_fails_loudly(host: str) -> None:
    _attempt(socket.getaddrinfo, host, 443)
    _attempt(socket.gethostbyname, host)
    _attempt(socket.gethostbyname_ex, host)
    _attempt(socket.create_connection, (host, 443), 1)


def test_an_http_client_that_swallows_the_error_still_leaves_a_trace() -> None:
    async def call() -> None:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            await client.get("https://gmail.googleapis.com/gmail/v1/users/me/profile")

    before = len(canary.reached)
    with pytest.raises((httpx.HTTPError, canary.RealGoogleReachedError)):
        run(call())
    # Whatever the library made of the exception, the attempt is written down, and the fixture
    # `no_real_google` would fail this test if the entry stayed.
    assert canary.reached[before:] == ["gmail.googleapis.com"]
    del canary.reached[before:]


def test_other_hosts_resolve_as_before_and_the_block_is_put_back() -> None:
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"
    assert not canary.is_google("googleapis.com.example.org")
    assert not canary.is_google("notgoogle.com")
    guarded = socket.getaddrinfo
    restore = canary.block_google()  # a second block on top of the session's
    assert socket.getaddrinfo is not guarded
    restore()
    assert socket.getaddrinfo is guarded


def test_the_canary_finds_a_marker_planted_in_a_log_line_and_in_a_redis_value(
    caplog: pytest.LogCaptureFixture, redis_url: str
) -> None:
    planted = canary.marker()
    absent = canary.marker()
    prefix = f"canary-test:{uuid.uuid4().hex}"

    # A log line: in the message, in an argument, in an extra field, in an exception.
    logger = logging.getLogger("tests.mail.canary")
    with caplog.at_level(logging.DEBUG, logger="tests.mail.canary"):
        logger.info("subject is %s", planted)
        logger.info("a line", extra={"nested": {"deep": [planted]}})
        try:
            raise RuntimeError(planted)
        except RuntimeError:
            logger.exception("it failed")
        logger.info("a clean line")
    in_logs = canary.find([planted, absent], logs=caplog.records)
    assert len(in_logs) == 3
    assert {found.marker for found in in_logs} == {planted}
    assert {found.where for found in in_logs} == {"log"}
    # The line the app's own formatter would write.
    line = JsonFormatter().format(caplog.records[1])
    assert [found.marker for found in canary.find([planted], logs=[line])] == [planted]
    assert canary.find([absent], logs=caplog.records) == []

    # A Redis value of every type, a key name, and a pickle as arq stores a result.
    client = redis_client.Redis.from_url(redis_url)
    try:
        client.set(f"{prefix}:string", f"before {planted} after")
        client.rpush(f"{prefix}:list", "clean", planted)
        client.hset(f"{prefix}:hash", "field", planted)
        client.sadd(f"{prefix}:set", planted)
        client.zadd(f"{prefix}:zset", {planted: 1.0})
        client.xadd(f"{prefix}:stream", {"body": planted})
        client.set(f"{prefix}:arq-result", pickle.dumps({"r": {"error": planted}, "s": False}))
        client.set(f"{prefix}:{planted}", "clean")
        client.set(f"{prefix}:clean", "nothing here")
        found = canary.find([planted, absent], redis=client, redis_match=f"{prefix}:*")
        assert {item.marker for item in found} == {planted}
        values = {
            item.place.removeprefix(prefix + ":") for item in found if item.where == "redis value"
        }
        assert values == {"string", "list", "hash", "set", "zset", "stream", "arq-result"}
        assert [item.place for item in found if item.where == "redis key"] == [
            f"{prefix}:{planted}"
        ]
        assert canary.find([absent], redis=client, redis_match=f"{prefix}:*") == []
    finally:
        for key in client.scan_iter(match=f"{prefix}:*"):
            client.delete(key)
        client.close()

    # Anything else a task hands over: rows, job arguments, an exception.
    rows = [{"id": 1, "error": "quota"}, ("m1", planted.encode()), RuntimeError(f"x {planted}")]
    assert [found.place for found in canary.find([planted], values=rows)] == ["1", "2"]
    assert canary.find([""], logs=[planted]) == []  # an empty marker is not a marker


def test_a_marker_is_random_and_long_enough_for_the_log_scrubber() -> None:
    from app.logging_config import HIDDEN_MIN

    first, second = canary.marker(), canary.marker("ya29.token")
    assert first != second
    assert len(first) >= HIDDEN_MIN + 16
    assert second.startswith("ya29.token-")


def test_the_loop_resolver_is_blocked_too() -> None:
    async def resolve() -> None:
        await asyncio.get_running_loop().getaddrinfo("oauth2.googleapis.com", 443)

    before = len(canary.reached)
    with pytest.raises(canary.RealGoogleReachedError):
        run(resolve())
    del canary.reached[before:]
