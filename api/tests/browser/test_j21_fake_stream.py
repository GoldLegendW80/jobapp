"""Jobs J21, round 1 of #809: the fake backend's web search stream (fake_streams/
web_discovery.json), the one every journey stack's sweep runs for "Anywhere on the web", ends the
web source searched with nothing new: one stubbed WebSearch, then no_match, no browserd call."""

import asyncio
import random
from typing import Any

from app.agents.models import RunStatus
from app.browser import finder, sources
from app.browser.sources import interactive
from tests.browser.runtime_support import (  # noqa: F401  (fixtures)
    Runtime,
    alice,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runs_of,
    runtime,
)


class NoBrowser:
    """browserd: the stream opens no page."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"ok": False, "error": "navigation_failed"}

    async def snapshot(self) -> dict[str, Any]:
        self.calls.append("browser_snapshot")
        return {"ok": False, "error": "no_page"}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        self.calls.append("browser_wait_for")
        return {"ok": True}


def test_the_fake_web_stream_searches_once_and_reports_no_match(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
) -> None:
    runtime.give_key(alice)
    browser = NoBrowser()
    pages = sources.Pages(
        browser,
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.Random(5),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: 0,
    )
    scope = finder.InteractiveScope(finder.WEB_START, ("ai engineer",), ("remote",), (), web=True)
    session = finder.FinderSession(pages, finder.WEB_START, "", str(alice), interactive=scope)
    asyncio.run(finder.RuntimeFinder(lambda: runtime.context())(session))
    [run] = runs_of(runtime.factory, alice)
    assert (run.agent, run.status) == (finder.WEB_NAME, RunStatus.done), run.error
    assert (session.web_searches, session.outcome, session.stopped) == (1, "no_match", "")
    assert interactive.outcome(session) is None  # searched, nothing new: no failure
    assert browser.calls == []
