"""Jobs J17: "Websites only" of Find jobs searches the ticked websites and no board, feed or
fixed reader, for that sweep only, and its public scope says so (app/jobs/sweep.py)."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus
from tests.identity.api_helpers import Api, Clock
from tests.jobs.sweep_helpers import board, read_sweep, search, set_profile
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_sweep_api import FakeQueue, fake_queue

__all__ = ["fake_queue"]

SITE = {
    "platform": "custom",
    "url": "https://jobs.example-robotics.test/",
    "enabled": True,
    "mode": "llm_browser",
}
FEED = {"kind": "remoteok", "enabled": True, "terms": {"terms": [], "advanced_regex": False}}
READER = {"kind": "vie", "query": "engineer", "location": "Paris", "enabled": True}


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j17-alice@example.org")


def start(api: Api, who: Signed, **kwargs: Any) -> Any:
    return api.request("POST", "/api/jobs/sweeps", token=who.token, **kwargs)


def everything(**overrides: Any) -> dict[str, Any]:
    """A search with every kind of source on: a board, a feed, a fixed reader and a website."""
    values: dict[str, Any] = {
        "feeds": [FEED],
        "browser_sources": [READER],
        "interactive_sites": [SITE],
        **overrides,
    }
    return search(board("fake-board"), **values)


def sweep_count(factory: sessionmaker[Session], who: Signed) -> int:
    with factory() as db:
        return db.query(Sweep).filter_by(user_id=who.id).count()


def test_websites_only_freezes_the_websites_alone_and_says_so(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, everything())
    created = start(api, alice, json={"profile_revision": 1, "websites_only": True})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["search_scope"]["websites_only"] is True
    assert body["search_scope"]["source_count"] == 1
    assert [(s["mode"], s["kind"]) for s in body["sources"]] == [("llm_browser", "custom")]
    scope = read_sweep(factory, uuid.UUID(body["id"])).stats["search_scope"]
    assert scope["websites_only"] is True
    assert [s["mode"] for s in scope["sources"]] == ["llm_browser"]
    # The saved settings are frozen whole: only this run leaves the other sources out.
    assert scope["search"]["boards"][0]["org"] == "fake-board"
    listed = api.request("GET", "/api/jobs/sweeps", token=alice.token).json()["items"][0]
    assert listed["search_scope"]["websites_only"] is True


@pytest.mark.parametrize("body", [None, {}, {"profile_revision": 1}, {"websites_only": False}])
def test_every_other_start_keeps_every_source(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
    body: dict[str, Any] | None,
) -> None:
    set_profile(factory, alice.id, everything())
    created = start(api, alice, **({} if body is None else {"json": body}))
    assert created.status_code == 202, created.text
    assert created.json()["search_scope"]["websites_only"] is False
    modes = sorted(s["mode"] for s in created.json()["sources"])
    assert modes == ["api", "feed", "fixed_reader", "llm_browser"]


@pytest.mark.parametrize("value", ["true", 1, None, [True]])
def test_websites_only_is_a_strict_boolean(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue, value: Any
) -> None:
    set_profile(factory, alice.id, everything())
    assert start(api, alice, json={"websites_only": value}).status_code == 422
    assert fake_queue.calls == []
    assert sweep_count(factory, alice) == 0


@pytest.mark.parametrize(
    "sites", [[], [{**SITE, "enabled": False}]], ids=["no website", "website off"]
)
def test_websites_only_without_a_ticked_website_starts_nothing(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
    sites: list[dict[str, Any]],
) -> None:
    set_profile(factory, alice.id, everything(interactive_sites=sites))
    refused = start(api, alice, json={"websites_only": True})
    assert refused.status_code == 422
    assert refused.json()["detail"] == sweep.NO_WEBSITES
    assert fake_queue.calls == []
    assert sweep_count(factory, alice) == 0
    # The same settings without the choice still search the board, the feed and the reader.
    assert start(api, alice).status_code == 202


def test_a_websites_only_run_reads_no_board_feed_or_fixed_reader(
    api: Api,
    clock: Clock,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
) -> None:
    set_profile(factory, alice.id, everything())
    sweep_id = uuid.UUID(start(api, alice, json={"websites_only": True}).json()["id"])
    plan = sweep.claim(factory, sweep_id, clock())
    assert plan is not None
    assert (plan.boards, plan.sources, plan.feeds) == ([], [], [])
    assert [site.url for site in plan.search.interactive_sites] == [SITE["url"]]
    with factory() as db:
        row = db.get(Sweep, sweep_id)
        row.stats = {key: value for key, value in row.stats.items() if key != "picked_at"}
        db.commit()

    class NoAts:
        async def fetch_board(self, *args: Any) -> None:
            pytest.fail("a websites only sweep read a board")

    async def no_feeds(*args: Any) -> None:
        pytest.fail("a websites only sweep read a feed")

    visited: list[tuple[list[Any], list[str]]] = []

    async def browser(plan: Any, *args: Any) -> None:
        visited.append((plan.sources, [s.url for s in plan.search.interactive_sites]))

    asyncio.run(
        sweep.run_sweep(factory, sweep_id, NoAts(), clock, browser=browser, feeds=no_feeds)
    )
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done, row.error
    assert visited == [([], [SITE["url"]])]
    assert row.stats["boards_total"] == 0


def test_a_frozen_websites_only_run_without_a_website_fails_with_the_websites_message(
    api: Api,
    clock: Clock,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
) -> None:
    set_profile(factory, alice.id, everything())
    sweep_id = uuid.UUID(start(api, alice, json={"websites_only": True}).json()["id"])
    with factory() as db:  # a scope whose website is off: never made by the api, fails closed
        row = db.get(Sweep, sweep_id)
        scope = dict(row.stats["search_scope"])
        scope["search"] = {**scope["search"], "interactive_sites": [{**SITE, "enabled": False}]}
        row.stats = {**row.stats, "search_scope": scope}
        db.commit()
    assert sweep.claim(factory, sweep_id, clock()) is None
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.failed
    assert row.error == sweep.NO_WEBSITES
