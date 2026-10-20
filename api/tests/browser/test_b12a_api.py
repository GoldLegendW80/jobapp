"""The sign in hand off (browser B12a) from the api and the worker: `app.browser.handoff`'s poll
and the routes of `app.browser.handoff_router`, on a real Redis (and Postgres for the routes),
with browserd's own control routes, lease manager and tool layer behind an httpx transport
(b3_support.FakeBrowserd) and a fake Chromium that records with or without CDP.

Two people, A and B: B can neither end nor resume A's hand off, sees no state of it, and A's
lease stays A's while B waits in the queue. No test sleeps: the poll's sleep is injected and moves
the shared clock; browserd's tick runs when the test says. Every account is `e2e-*@example.org`.
"""

import asyncio
import logging
import secrets
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.browser import handoff as worker_handoff
from app.browser.client import BrowserError
from app.browser.handoff import Outcome, request_signin
from app.browser.handoff_router import NO_HANDOFF
from app.browser.leases import HOLDER, MAX_GRACE_S, DbProfiles, Leases
from app.browser.tools import LeaseTools
from app.main import create_app
from tests.browser.b3_support import (
    IDLE_S,
    SESSION_MAX_S,
    Clock,
    FakeBrowserd,
    FakeChromium,
    World,
    flush,
    redis_url,
)
from tests.browser.test_b3_api import SECRET_KEY, USER_A, USER_B, no_removals, sign_in
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import handoff, live  # noqa: E402
from browserd.leases import Request  # noqa: E402

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url", "no_removals", "redis_url"]

HOST = "jobs.example.org"
FORM = f"https://{HOST}/apply/42?step=2"
A, B = str(uuid.uuid4()), str(uuid.uuid4())


# --- a Chromium with pages, as the tool layer reaches them ---------------------------------------


class Page:
    def __init__(self, url: str, title: str) -> None:
        self.url, self.name, self.closed = url, title, False

    @property
    def main_frame(self) -> Any:
        return SimpleNamespace(url=self.url, parent_frame=None)

    async def bring_to_front(self) -> None:
        return None

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return self.name

    async def goto(self, url: str, **_: Any) -> None:
        self.url = url

    async def close(self) -> None:
        self.closed = True


class Context:
    def __init__(self, page: Page) -> None:
        self.pages = [page]

    async def route(self, *_: Any) -> None:
        return None

    def on(self, *_: Any) -> None:
        return None

    async def new_page(self) -> Page:
        self.pages.append(Page("about:blank", ""))
        return self.pages[-1]


class Chromium(FakeChromium):
    def __init__(self, request: Request, title: str) -> None:
        super().__init__()
        self.request = request
        self.browser = SimpleNamespace(contexts=[Context(Page(FORM, title))])


class Site:
    """What the fixture site shows: who is signed in, set when the person signs in."""

    def __init__(self) -> None:
        self.signed_in = ""


def with_pages(browserd: FakeBrowserd, site: Site) -> list[Chromium]:
    """browserd's start, with Chromiums that have a page; the list of every start."""
    started: list[Chromium] = []

    async def start(request: Request, _: str) -> Chromium:
        assert all(c.stopped or c.killed for c in started), "two Chromiums at once"
        title = f"Signed in as {site.signed_in}" if site.signed_in else "Sign in to continue"
        started.append(Chromium(request, title))
        return started[-1]

    browserd.manager._start = start
    handoff.bind(browserd.service)
    return started


def watch(browserd: FakeBrowserd, lease_id: str) -> None:
    viewer = live.Viewer(lease_id, str(uuid.uuid4()), writer=None)  # type: ignore[arg-type]
    live.live_of(browserd.service).viewers.add(viewer)


@pytest.fixture
def world(redis_url: str) -> World:
    flush(redis_url)
    return World(redis_url)


def run(world: World, scenario: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        try:
            return await scenario()
        finally:
            await world.close()

    return asyncio.run(go())


async def lease_of(world: World, user: str = A, purpose: str = "apply_fill") -> LeaseTools:
    got = await world.store.acquire(user, purpose, True, hosts=[HOST])
    assert got.lease is not None
    assert got.token is not None
    client = world.store.client
    return LeaseTools(world.store, client, user, got.lease.lease_id, got.token)


def person(world: World, *steps: Callable[[], Awaitable[Any]], every_s: float = 60) -> Any:
    """The poll's sleep: each call moves the clock `every_s`, runs browserd's tick, then the
    person's next step, if any."""
    queue = list(steps)
    calls: list[float] = []

    async def sleep(seconds: float) -> None:
        calls.append(seconds)
        world.clock.advance(every_s)
        await world.browserd.manager.tick()
        if queue:
            await queue.pop(0)()

    sleep.calls = calls  # type: ignore[attr-defined]
    return sleep


async def nothing() -> None:
    return None


# --- the worker's poll --------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ["sweep", "full_auto"])
def test_a_sweep_or_full_auto_lease_answers_nobody_watching_at_once(
    world: World, purpose: str
) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        tools = await lease_of(world, purpose=purpose)
        watch(world.browserd, tools.lease_id)
        sleep = person(world)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome == Outcome("nobody_watching")
        assert outcome.needs_person
        assert "Stop here" in outcome.text()
        assert sleep.calls == []
        assert len(started) == 1
        assert not started[0].stopped
        assert (await world.store.current(A)).lease.state == "active"

    run(world, go)


def test_an_apply_lease_with_no_viewer_answers_nobody_watching_at_once(world: World) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        tools = await lease_of(world)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock)
        assert outcome.code == "nobody_watching"
        assert len(started) == 1

    run(world, go)


def test_a_wait_of_25_minutes_on_a_15_minute_lease_then_4_minutes_of_work(world: World) -> None:
    async def go() -> None:
        site = Site()
        started = with_pages(world.browserd, site)
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        for _ in range(10):  # ten minutes of work, a tool call each minute
            world.clock.advance(60)
            await tools.snapshot()
        waited: list[Any] = []

        async def check() -> None:
            mine = (await world.store.current(A)).lease
            waited.append(mine)
            browserd_lease = world.browserd.manager.current()
            assert mine is not None
            assert mine.state == "waiting_person"
            assert browserd_lease is not None
            assert browserd_lease.state == "waiting_person"

        async def signs_in() -> None:
            site.signed_in = "e2e-browser-a"
            await world.browserd.client().handoff(tools.lease_id, "done")

        sleep = person(world, *([check] * 25), signs_in)
        outcome = await request_signin(
            tools, "Example Portal", "The form needs an account.", clock=world.clock, sleep=sleep
        )
        assert outcome.code == "signed_in"
        assert (
            outcome.text() == 'The person signed in; the page is now "Signed in as e2e-browser-a".'
        )
        assert len(waited) == 25
        assert waited[0].wait_site == "Example Portal"
        assert [c.request.automation for c in started] == [True, False, True]
        assert started[1].request.address == FORM
        mine = (await world.store.current(A)).lease
        assert mine is not None
        assert mine.state == "active"
        for _ in range(4):  # four more minutes of work
            world.clock.advance(60)
            await tools.snapshot()
            assert (await world.store.current(A)).lease is not None
        assert world.browserd.manager.current().state == "active"

    run(world, go)


def test_cancel_ends_the_wait_signin_cancelled(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)

        async def cancels() -> None:
            await world.browserd.client().handoff(tools.lease_id, "cancel")

        sleep = person(world, nothing, cancels)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome.code == "signin_cancelled"
        assert outcome.needs_person
        assert world.browserd.manager.current() is None

    run(world, go)


def test_thirty_minutes_without_a_click_end_the_lease_signin_timeout(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        sleep = person(world)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome.code == "signin_timeout"
        assert len(sleep.calls) == SESSION_MAX_S // 60
        assert world.browserd.manager.last_end["reason"] == "signin_timeout"
        assert (await world.store.current(A)).lease is None

    run(world, go)


def test_a_browserd_that_stops_answering_never_holds_the_wait_past_its_deadline(
    world: World,
) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)

        async def goes_down() -> None:
            world.browserd.down = True

        sleep = person(world, nothing, goes_down)
        began = world.clock()
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome.code == "signin_timeout"
        assert world.clock() - began <= SESSION_MAX_S + MAX_GRACE_S + 60
        assert await (await world.redis()).get(HOLDER) is None  # released in the api

    run(world, go)


def test_a_second_call_while_one_waits_follows_the_same_hand_off(world: World) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        first = await request_signin(
            tools, "Example Portal", "x", budget_s=60, clock=world.clock, sleep=person(world)
        )
        assert first.code == "turn_ended"  # the budget ran out, the lease still waits
        assert world.browserd.manager.current().state == "waiting_person"
        deadline = (await world.store.current(A)).lease.wait_deadline

        async def done() -> None:
            await world.browserd.client().handoff(tools.lease_id, "done")

        again = await request_signin(
            tools, "Example Portal", "x", clock=world.clock, sleep=person(world, done)
        )
        assert again.code == "signed_in"
        assert len(started) == 3  # one restart without CDP, one with
        assert deadline is not None

    run(world, go)


def test_a_second_call_polls_until_browserds_deadline_not_a_fresh_one(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        began = world.clock()
        first = await request_signin(
            tools, "Example Portal", "x", budget_s=1000, clock=world.clock, sleep=person(world)
        )
        assert first.code == "turn_ended"

        async def goes_down() -> None:
            world.browserd.down = True

        again = await request_signin(
            tools, "Example Portal", "x", clock=world.clock, sleep=person(world, goes_down)
        )
        assert again.code == "signin_timeout"
        # One deadline from the first call: a fresh one would end past 1000 + SESSION_MAX_S.
        assert world.clock() - began <= SESSION_MAX_S + MAX_GRACE_S + 60

    run(world, go)


def test_a_swap_that_fails_ends_the_lease_and_the_call_at_once(world: World) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        start = world.browserd.manager._start

        async def only_once(request: Request, lease_id: str) -> Any:
            if started:
                raise RuntimeError("Chromium did not start")
            return await start(request, lease_id)

        world.browserd.manager._start = only_once
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        sleep = person(world)
        with pytest.raises(BrowserError) as ended:  # browserd's 410: the lease ended in the call
            await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert ended.value.code == "ended"
        assert sleep.calls == []  # type: ignore[attr-defined]
        assert world.browserd.manager.last_end["reason"] == "browser_failed"

    run(world, go)


def test_a_bare_tool_call_of_the_sign_in_is_refused_before_browserd(world: World) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        answer = await tools.call("browser_request_signin", {"site": "Example Portal"})
        assert answer["ok"] is False
        assert answer["error"] == "bad_tool"
        assert len(started) == 1  # Chromium never restarted
        assert world.browserd.manager.current().state == "active"
        assert (await world.store.current(A)).lease.state == "active"

    run(world, go)


def test_the_lease_stays_as_while_b_waits_in_the_queue(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world, A)

        async def b_asks() -> None:
            got = await world.store.acquire(B, "apply_fill", True)
            assert got.lease is None
            assert got.waiting is not None
            view = await world.store.current(A)
            assert view.lease is not None
            assert view.lease.state == "waiting_person"

        async def done() -> None:
            await world.browserd.client().handoff(tools.lease_id, "done")

        watch(world.browserd, tools.lease_id)
        sleep = person(world, b_asks, b_asks, done)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome.code == "signed_in"
        assert world.browserd.manager.current().request.user_id == A
        b_view = await world.store.current(B)
        assert b_view.lease is None
        assert b_view.held_by_other

    run(world, go)


def test_redis_restarted_during_the_wait_keeps_the_same_deadline(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        seen: list[float | None] = []

        async def note() -> None:
            seen.append((await world.store.current(A)).lease.wait_deadline)

        async def restart() -> None:
            await world.flush()  # Redis lost every key
            seen.append((await world.store.current(A)).lease.wait_deadline)

        sleep = person(world, note, *([nothing] * 9), restart)
        outcome = await request_signin(
            tools, "Example Portal", "x", budget_s=12 * 60, clock=world.clock, sleep=sleep
        )
        assert outcome.code == "turn_ended"
        assert seen[0] is not None
        assert seen == [seen[0], seen[0]]
        lease = (await world.store.current(A)).lease
        assert lease.state == "waiting_person"
        assert lease.wait_site == "Example Portal"

    run(world, go)


def test_a_crash_during_the_hand_off_ends_browser_crashed(world: World) -> None:
    async def go() -> None:
        started = with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)

        async def crashes() -> None:
            await world.browserd.manager.release(tools.lease_id, "browser_crashed")

        sleep = person(world, crashes)
        outcome = await request_signin(tools, "Example Portal", "x", clock=world.clock, sleep=sleep)
        assert outcome.code == "ended"
        assert not outcome.needs_person
        assert started[1].killed
        assert not started[1].stopped

    run(world, go)


def test_the_canary_reason_and_title_are_in_no_log_nor_redis(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "cnry" + secrets.token_hex(8)

    async def go() -> None:
        site = Site()
        with_pages(world.browserd, site)
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)

        async def done() -> None:
            site.signed_in = marker
            await world.browserd.client().handoff(tools.lease_id, "done")

        sleep = person(world, done)
        with caplog.at_level(logging.DEBUG):
            outcome = await request_signin(
                tools, "Example Portal", f"reason {marker}", clock=world.clock, sleep=sleep
            )
        assert marker in outcome.title  # the agent's own result, nowhere else
        assert marker not in await world.dump()
        assert marker not in caplog.text

    run(world, go)


def test_a_refusal_of_the_tool_is_an_outcome_with_browserds_sentence(world: World) -> None:
    async def go() -> None:
        with_pages(world.browserd, Site())
        tools = await lease_of(world)
        watch(world.browserd, tools.lease_id)
        outcome = await request_signin(tools, "", "x", clock=world.clock)
        assert outcome.code == "bad_args"
        assert outcome.detail
        assert outcome.text().startswith("The sign in could not be asked: ")
        other = LeaseTools(world.store, world.store.client, B, tools.lease_id, "x")
        with pytest.raises(BrowserError):  # a lease of another person, refused before browserd
            await request_signin(other, "Example Portal", "x", clock=world.clock)

    run(world, go)


def test_outcome_texts_have_no_dash_separators() -> None:
    for code in [*worker_handoff.TEXTS, "signed_in", "bad_args"]:
        text = Outcome(code, title="Example Portal").text()
        assert " - " not in text
        assert "—" not in text
        assert "–" not in text


# --- the routes ---------------------------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def browserd(clock: Clock) -> FakeBrowserd:
    return FakeBrowserd(clock, "e2e-browserd-" + uuid.uuid4().hex)


@pytest.fixture
def app(migrated_url: str, redis_url: str, browserd: FakeBrowserd, clock: Clock) -> FastAPI:
    flush(redis_url)
    made = create_app(make_settings(migrated_url, secret_key=SECRET_KEY, redis_url=redis_url))
    made.state.browser_leases = Leases(
        made.state.queue.pool,
        browserd.client(),
        DbProfiles(made.state.sessionmaker),
        idle_s=IDLE_S,
        session_max_s=SESSION_MAX_S,
        clock=clock,
    )
    return made


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


@pytest.fixture
def user_a(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_A)


@pytest.fixture
def user_b(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_B)


def waiting(api: Api, app: FastAPI, browserd: FakeBrowserd, user_id: uuid.UUID) -> list[Any]:
    """An agent's lease of the person that waits for them to sign in, as the worker starts it;
    the Chromiums browserd started."""
    started = with_pages(browserd, Site())
    store: Leases = app.state.browser_leases

    async def go() -> None:
        got = await store.acquire(str(user_id), "apply_fill", True, hosts=[HOST])
        assert got.lease is not None
        assert got.token is not None
        watch(browserd, got.lease.lease_id)
        tools = LeaseTools(store, store.client, str(user_id), got.lease.lease_id, got.token)
        answer = await tools.ask_signin("Example Portal", "")
        assert answer["error"] == "waiting_person"
        await store.wait_person(str(user_id), got.lease.lease_id, "signin", "Example Portal")

    api.client.portal.call(go)  # type: ignore[union-attr]
    assert len(started) == 2
    return started


def test_done_restarts_chromium_with_cdp_and_resumes_the_lease(
    api: Api, app: FastAPI, browserd: FakeBrowserd, clock: Clock, user_a: tuple[uuid.UUID, str]
) -> None:
    started = waiting(api, app, browserd, user_a[0])
    shown = api.get("/api/browser", token=user_a[1]).json()["lease"]
    assert (shown["state"], shown["wait_reason"], shown["wait_site"]) == (
        "waiting_person",
        "signin",
        "Example Portal",
    )
    assert shown["wait_minutes_left"] == SESSION_MAX_S // 60
    clock.advance(10 * 60 + 1)
    assert api.get("/api/browser", token=user_a[1]).json()["lease"]["wait_minutes_left"] == 20
    answer = api.post("/api/browser/handoff/done", token=user_a[1])
    assert answer.status_code == 204, answer.text
    assert answer.headers["cache-control"] == "no-store"
    assert [c.request.automation for c in started] == [True, False, True]
    shown = api.get("/api/browser", token=user_a[1]).json()["lease"]
    assert (shown["state"], shown["wait_minutes_left"]) == ("active", None)
    assert api.post("/api/browser/handoff/done", token=user_a[1]).status_code == 204  # twice
    assert len(started) == 3  # one restart


def test_cancel_releases_the_lease(
    api: Api, app: FastAPI, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    started = waiting(api, app, browserd, user_a[0])
    assert api.post("/api/browser/handoff/cancel", token=user_a[1]).status_code == 204
    assert api.get("/api/browser", token=user_a[1]).json()["lease"] is None
    assert browserd.manager.last_end["reason"] == "signin_cancelled"
    assert started[1].stopped
    assert browserd.manager.current() is None
    answer = api.post("/api/browser/handoff/cancel", token=user_a[1])
    assert (answer.status_code, answer.json()["detail"]) == (404, NO_HANDOFF)


def test_b_can_neither_see_nor_end_as_hand_off(
    api: Api,
    app: FastAPI,
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    user_b: tuple[uuid.UUID, str],
) -> None:
    started = waiting(api, app, browserd, user_a[0])
    seen = api.get("/api/browser", token=user_b[1])
    assert seen.json()["lease"] is None
    assert seen.json()["holder"] == "other"
    assert "Example Portal" not in seen.text
    for action in ("done", "cancel"):
        answer = api.post(f"/api/browser/handoff/{action}", token=user_b[1])
        assert (answer.status_code, answer.json()["detail"]) == (404, NO_HANDOFF)
    assert len(started) == 2
    assert api.get("/api/browser", token=user_a[1]).json()["lease"]["state"] == "waiting_person"
    assert browserd.manager.current().state == "waiting_person"


def test_no_hand_off_signed_out_or_a_persons_own_session(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    for action in ("done", "cancel"):
        assert api.post(f"/api/browser/handoff/{action}").status_code == 401
        assert api.post(f"/api/browser/handoff/{action}", token=user_a[1]).status_code == 404
    with_pages(browserd, Site())
    session = api.post("/api/browser/sessions", token=user_a[1], json={"purpose": "look"})
    assert session.status_code == 201
    for action in ("done", "cancel"):
        assert api.post(f"/api/browser/handoff/{action}", token=user_a[1]).status_code == 404
    assert browserd.manager.current() is not None  # the person's own session goes on


def test_a_browserd_that_cannot_be_reached_answers_503(
    api: Api, app: FastAPI, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    waiting(api, app, browserd, user_a[0])
    browserd.down = True
    answer = api.post("/api/browser/handoff/done", token=user_a[1])
    assert answer.status_code == 503
    browserd.down = False
    assert browserd.manager.current().state == "waiting_person"
