"""Jobs J21, round 1 of #809: the fake backend's web search stream (fake_streams/
web_discovery.json), the one every journey stack's sweep runs for "Anywhere on the web": one
stubbed WebSearch of the query that covers the run's plan (jobs J23, round 2 of #814:
`{web_plan_query}`, web_plan.covering_query), answered with results, then no_match, accepted,
no browserd call: searched with nothing new, whatever the scope. Its variant
web_discovery_failing.json answers each WebSearch with an error: the source then ends
`web_search_failed` and the failed queries count for no plan (round 1 of #814). A refusal of the
gate is no failure of the search engine (round 2)."""

import asyncio
import json
import random
from dataclasses import replace
from typing import Any

import pytest

from app.agents.models import RunStatus
from app.browser import finder, sources, web_plan
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


PROFILE = json.dumps(
    {
        "recent_roles": ["GenAI Engineer"],
        "skills": [{"name": "LLM agents", "terms": ["Python", "FastAPI", "React"]}],
    }
)


def run_web(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    places: tuple[str, ...],
    script: str | None = None,
    profile: str = "{}",
    agents: dict[str, Any] | None = None,
) -> tuple[finder.FinderSession, NoBrowser]:
    runtime.give_key(alice)
    browser = NoBrowser()
    pages = sources.Pages(
        browser,
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.Random(5),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: 0,
    )
    scope = finder.InteractiveScope(
        finder.WEB_START, ("ai engineer",), places, (), profile=profile, web=True
    )
    session = finder.FinderSession(pages, finder.WEB_START, "", str(alice), interactive=scope)

    def context():
        made = runtime.context()
        return replace(made, agents=agents) if agents else made

    asyncio.run(finder.RuntimeFinder(context, script=script)(session))
    [run] = runs_of(runtime.factory, alice)
    assert (run.agent, run.status) == (finder.WEB_NAME, RunStatus.done), run.error
    return session, browser


@pytest.mark.parametrize(
    ("places", "profile"),
    [
        (("La Réunion", "Guadeloupe", "Tahiti"), PROFILE),
        ((), PROFILE),
        (("remote",), "{}"),
        (("Lyon, France", "Bruxelles"), "{}"),
    ],
)
def test_the_fake_web_stream_searches_its_plan_and_finds_nothing_new(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    places: tuple[str, ...],
    profile: str,
) -> None:
    session, browser = run_web(runtime, alice, places, profile=profile)
    query = web_plan.covering_query(session.web_plan())
    assert query
    assert (session.web_searches, session.outcome, session.stopped) == (1, "no_match", "")
    assert session.web_search_failures == 0
    assert (session.web_queries, session.web_pending) == ([query], {})  # counted once answered
    assert session.web_left() == ""  # the plan is searched: accepted on the first try
    assert interactive.outcome(session) is None  # searched with nothing new
    assert browser.calls == []


def test_the_failing_web_stream_ends_web_search_failed(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
) -> None:
    session, browser = run_web(runtime, alice, ("remote",), script="web_discovery_failing")
    assert (session.web_searches, session.outcome, session.stopped) == (2, "no_match", "")
    assert session.web_search_failures == finder.WEB_SEARCH_FAILS  # the runner told each error
    assert (session.web_queries, session.web_pending) == ([], {})  # errors count for nothing
    assert interactive.outcome(session) == "web_search_failed"
    assert browser.calls == []


def test_a_refusal_of_the_gate_is_no_failed_web_search(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
) -> None:
    """Round 2 of #814: a spent cap (WEB_SEARCHES_SPENT) refuses both searches before the
    engine answers: no failure is counted and the plan is not lifted."""
    capped = replace(finder.WEB_FINDER, web_searches_max=0)
    session, _ = run_web(
        runtime,
        alice,
        ("remote",),
        script="web_discovery_failing",
        agents={finder.WEB_NAME: capped},
    )
    assert session.web_searches == 0  # the gate refused before the session's hook
    assert session.web_search_failures == 0
    assert session.web_left() != ""  # the plan still holds
    assert interactive.outcome(session) != "web_search_failed"
