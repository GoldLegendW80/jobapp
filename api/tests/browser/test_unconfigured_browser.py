"""A browserd client without its control secret (production, 2026-10-27): every call fails
`unavailable` before any request, and says why in the log, at most once a minute per client, never
with a secret's value. Before, nothing was logged: Sign in answered 503 while the deploy said the
browser was healthy, and neither log of the stack named the cause."""

import asyncio
import logging

import httpx
import pytest

from app.browser.client import UNCONFIGURED_EVERY_S, BrowserdClient, BrowserError
from tests.browser.b3_support import Clock

NOT_CONFIGURED = "browserd not configured: BROWSER_CONTROL_SECRET is empty"


def asked_nothing(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a request without a secret: {request.url}")


def said(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.browser.client" and r.getMessage().startswith(NOT_CONFIGURED)
    ]


@pytest.mark.parametrize("secret", [None, "", "  ", "\t\n"])
def test_a_client_without_a_secret_asks_nothing_and_says_why_once_a_minute(
    secret: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    client = BrowserdClient(
        "http://browser:9300", secret, httpx.MockTransport(asked_nothing), clock=clock
    )
    assert client.configured is False

    def start() -> None:
        with pytest.raises(BrowserError) as raised:
            asyncio.run(
                client.start(
                    user_id="e2e-user",
                    profile_id="00000000-0000-0000-0000-000000000001",
                    uid=20131,
                    purpose="signin",
                    automation=False,
                    address="https://fr.indeed.com/",
                )
            )
        assert raised.value.code == "unavailable"

    with caplog.at_level(logging.INFO, logger="app.browser.client"):
        start()
        assert said(caplog) == [f"{NOT_CONFIGURED} (POST /leases)"]
        assert [r.levelname for r in caplog.records if r.name == "app.browser.client"] == [
            "WARNING"
        ]
        # The page polls, the worker sweeps: one line a minute is enough to find the cause.
        start()
        with pytest.raises(BrowserError):
            asyncio.run(client.current())
        clock.advance(UNCONFIGURED_EVERY_S - 1)
        start()
        assert len(said(caplog)) == 1
        clock.advance(1)
        with pytest.raises(BrowserError):
            asyncio.run(client.current())
        assert said(caplog) == [
            f"{NOT_CONFIGURED} (POST /leases)",
            f"{NOT_CONFIGURED} (GET /leases/current)",
        ]


def test_a_configured_client_never_says_it_is_not_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "e2e-secret-" + "7" * 24
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"lease": None})

    client = BrowserdClient("http://browser:9300", marker, httpx.MockTransport(answer))
    assert client.configured is True
    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(client.current())["lease"] is None
    assert seen == [f"Bearer {marker}"]
    assert said(caplog) == []
    assert marker not in caplog.text
