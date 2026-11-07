"""Two browsers at once (owner, 2026-11-06): browserd's slots without a browser. One person's
agent leases share one Chromium in BROWSER_SLOTS slots, each with its own lease, window, live
view and tools; nobody else and no person's own session joins; a slot's end closes its window
only; the display's one keyboard goes to one slot at a time and every agent waits meanwhile.
The pieces that need Chromium and Xvfb are the container tests (test_browser_container.py).
"""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import chromium, handoff, live  # noqa: E402
from browserd import config as c  # noqa: E402
from browserd.leases import LeaseEnded, LeaseError, Manager, Request  # noqa: E402
from browserd.tools import actions  # noqa: E402

PROFILE = "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11"
OTHER_PROFILE = "0b7c6d1e-2f3a-4b5c-8d6e-7f8091a2b3c4"
VIEWER = "11111111-2222-4333-8444-555555555555"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeBrowser:
    def __init__(self) -> None:
        self.stopped = self.killed = False
        self.ended_slots: list[str] = []

    async def stop(self) -> None:
        self.stopped = True

    async def kill(self) -> None:
        self.killed = True

    async def end_slot(self, lease_id: str) -> None:
        self.ended_slots.append(lease_id)


def request(
    purpose: str = "apply_fill", *, user: str = "a", profile: str = PROFILE, uid: int = 20001
) -> Request:
    return Request.parse({"user_id": user, "profile_id": profile, "uid": uid, "purpose": purpose})


def manager(clock: Clock, slots: int = 2, **config: Any) -> tuple[Manager, list[FakeBrowser]]:
    started: list[FakeBrowser] = []

    async def start(_: Request, __: str) -> FakeBrowser:
        started.append(FakeBrowser())
        return started[-1]

    return Manager(c.Config(secret=b"s", slots=slots, **config), start, clock), started


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- the setting ----------------------------------------------------------------------------------


def test_browser_slots_defaults_to_two_and_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSER_SLOTS", raising=False)
    assert c.Config.from_env().slots == 2
    monkeypatch.setenv("BROWSER_SLOTS", "1")
    assert c.Config.from_env().slots == 1
    for bad in ("0", "3"):
        monkeypatch.setenv("BROWSER_SLOTS", bad)
        with pytest.raises(ValueError, match="BROWSER_SLOTS"):
            c.Config.from_env()
    assert c.Config().slots == 1  # the dataclass's default: one browser, as before


def test_the_display_is_one_region_per_slot() -> None:
    assert c.screen(1) == c.SCREEN == "1440x900x24"
    assert c.screen(2) == "2880x900x24"
    assert [c.clip(0), c.clip(1)] == ["1440x900+0+0", "1440x900+1440+0"]


# --- the leases -----------------------------------------------------------------------------------


def test_a_second_agent_lease_of_the_person_joins_in_the_next_slot() -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        first, token_a = await mgr.acquire(request("sweep"))
        second, token_b = await mgr.acquire(request("apply_fill"))
        assert len(started) == 1  # one Chromium for both
        assert (first.request.slot, second.request.slot) == (0, 1)
        assert (first.public(clock())["slot"], second.public(clock())["slot"]) == (0, 1)
        assert token_a != token_b
        assert first.lease_id != second.lease_id
        assert mgr.check_token(second.lease_id, token_b) is second
        with pytest.raises(LeaseError) as full:
            await mgr.acquire(request("full_auto"))
        assert (full.value.status, full.value.code) == (409, "busy")
        assert full.value.extra == {"holder_purpose": "sweep"}

    run(go())


def test_the_caller_never_chooses_its_slot() -> None:
    body = {"user_id": "a", "profile_id": PROFILE, "uid": 20001, "purpose": "sweep", "slot": 1}
    assert Request.parse(body).slot == 0


@pytest.mark.parametrize(
    "joiner",
    [
        request("apply_fill", user="b", profile=OTHER_PROFILE, uid=20002),  # another person
        request("takeover"),  # the person's own session never shares the browser
        request("signin"),
        request("apply_manual"),  # Apply myself: the person's input from start to end
    ],
)
def test_nobody_else_and_no_session_of_the_person_joins(joiner: Request) -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        await mgr.acquire(request("sweep"))
        with pytest.raises(LeaseError) as refused:
            await mgr.acquire(joiner)
        assert (refused.value.status, refused.value.code) == (409, "busy")
        assert len(started) == 1
        assert len(mgr.held()) == 1

    run(go())


def test_an_agent_never_joins_a_person_session() -> None:
    clock = Clock()
    mgr, _ = manager(clock)

    async def go() -> None:
        await mgr.acquire(request("signin"))
        with pytest.raises(LeaseError):
            await mgr.acquire(request("sweep"))

    run(go())


def test_one_slot_keeps_the_rule_of_one_lease_at_a_time() -> None:
    clock = Clock()
    mgr, _ = manager(clock, slots=1)

    async def go() -> None:
        await mgr.acquire(request("sweep"))
        with pytest.raises(LeaseError) as refused:
            await mgr.acquire(request("apply_fill"))
        assert refused.value.code == "busy"

    run(go())


def test_no_lease_joins_a_browser_that_is_still_starting() -> None:
    clock = Clock()
    gate = asyncio.Event()

    async def go() -> None:
        async def slow(_: Request, __: str) -> FakeBrowser:
            await gate.wait()
            return FakeBrowser()

        mgr = Manager(c.Config(secret=b"s", slots=2), slow, clock)
        first = asyncio.ensure_future(mgr.acquire(request("sweep")))
        await asyncio.sleep(0)
        with pytest.raises(LeaseError) as refused:
            await mgr.acquire(request("apply_fill"))
        assert refused.value.code == "busy"
        gate.set()
        lease, _ = await first
        assert lease.request.slot == 0

    run(go())


def test_no_lease_joins_while_a_slot_is_handed_to_the_person() -> None:
    clock = Clock()
    mgr, _ = manager(clock)

    async def go() -> None:
        lease, _ = await mgr.acquire(request("apply_agent"))
        lease.person_input = True  # B12: its Chromium runs with no debugging port now
        with pytest.raises(LeaseError):
            await mgr.acquire(request("sweep"))

    run(go())


def test_a_slot_ends_alone_and_the_browser_ends_with_the_last() -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        first, _ = await mgr.acquire(request("sweep"))
        second, token = await mgr.acquire(request("apply_fill"))
        assert await mgr.release(first.lease_id, "released")
        browser = started[0]
        assert browser.ended_slots == [first.lease_id]
        assert not browser.stopped
        assert mgr.browser is browser
        assert mgr.last_end == {"lease_id": first.lease_id, "reason": "released"}
        assert mgr.check_token(second.lease_id, token) is second
        # The freed slot is the next lease's: slot 0, beside the one still running.
        third, _ = await mgr.acquire(request("sweep"))
        assert third.request.slot == 0
        assert len(started) == 1
        await mgr.release(second.lease_id, "released")
        await mgr.release(third.lease_id, "released")
        assert browser.stopped
        assert mgr.browser is None
        assert not mgr.held()

    run(go())


def test_the_idle_timer_ends_one_slot_and_the_other_goes_on() -> None:
    clock = Clock()
    mgr, started = manager(clock, idle_s=300)

    async def go() -> None:
        quiet, _ = await mgr.acquire(request("sweep"))
        busy, _ = await mgr.acquire(request("apply_fill"))
        for _ in range(5):
            clock.now += 60
            busy.touch(clock())
        assert await mgr.tick() == "idle"
        assert mgr.get(quiet.lease_id) is None
        assert mgr.get(busy.lease_id) is busy
        assert not started[0].stopped

    run(go())


def test_a_crash_of_the_shared_chromium_ends_every_slot() -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        await mgr.acquire(request("sweep"))
        await mgr.acquire(request("apply_fill"))
        other = FakeBrowser()
        assert not await mgr.release_browser(other, "browser_crashed")  # not the running one
        assert len(mgr.held()) == 2
        assert await mgr.release_browser(started[0], "browser_crashed")
        assert not mgr.held()
        assert started[0].killed
        assert not started[0].ended_slots

    run(go())


def test_the_calls_of_an_ended_slot_end_and_the_other_slot_s_go_on() -> None:
    clock = Clock()
    mgr, _ = manager(clock)

    async def go() -> None:
        first, token_a = await mgr.acquire(request("sweep"))
        second, token_b = await mgr.acquire(request("apply_fill"))
        hold = asyncio.Event()

        async def work() -> str:
            await hold.wait()
            return "done"

        call_a = asyncio.ensure_future(mgr.call(first.lease_id, token_a, work))
        call_b = asyncio.ensure_future(mgr.call(second.lease_id, token_b, work))
        await asyncio.sleep(0)
        await mgr.release(first.lease_id, "idle")
        with pytest.raises(LeaseEnded) as ended:
            await call_a
        assert ended.value.reason == "idle"
        hold.set()
        assert await call_b == "done"

    run(go())


def test_the_hand_off_never_restarts_a_shared_browser() -> None:
    clock = Clock()
    mgr, started = manager(clock)

    async def go() -> None:
        first, _ = await mgr.acquire(request("apply_agent"))
        await mgr.acquire(request("sweep"))
        assert not await mgr.swap(first, first.request)
        assert len(started) == 1
        assert mgr.get(first.lease_id) is first

        class Svc:
            manager = mgr

        assert not handoff.watched(Svc(), first)  # a sign in waits for later, as unwatched

    run(go())


# --- Chromium's switches --------------------------------------------------------------------------


def test_a_shared_agent_chromium_carries_the_sweep_switches_and_its_slot_position() -> None:
    from dataclasses import replace

    fill = replace(request("apply_fill"), slot=1)
    alone = chromium.switches(request("apply_fill"), "/leases/x", 9333)
    shared = chromium.switches(fill, "/leases/x", 9333, shared=True)
    assert "--start-maximized" in alone
    assert "--window-position=0,0" in alone
    assert not set(chromium.WEBGL_SWITCHES) & set(alone)
    assert "--start-maximized" not in shared
    assert "--window-position=1440,0" in shared
    assert set(chromium.WEBGL_SWITCHES) <= set(shared)
    assert any(s.endswith(",IsolateSandboxedIframes") for s in shared)
    person = chromium.switches(request("signin"), "/leases/x", None, shared=True)
    assert not set(chromium.WEBGL_SWITCHES) & set(person)
    assert not any("IsolateSandboxedIframes" in s for s in person)


# --- the slots' contexts --------------------------------------------------------------------------


class FakeContext:
    def __init__(
        self, name: str, cookies: list[dict] | None = None, origins: list[dict] | None = None
    ) -> None:
        self.name, self.pages, self.closed = name, [], False
        self.jar = list(cookies or [])
        self.origins = list(origins or [])  # local storage by origin, as Playwright states it
        self.handlers: list[Any] = []
        self.state_fails = False

    async def storage_state(self) -> dict:
        if self.state_fails:
            raise RuntimeError("Target page, context or browser has been closed")
        return {"cookies": list(self.jar), "origins": list(self.origins)}

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))

    async def cookies(self) -> list[dict]:
        return list(self.jar)

    async def add_cookies(self, cookies: list[dict]) -> None:
        self.jar.extend(cookies)

    async def close(self) -> None:
        self.closed = True


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context, self.is_closed_ = context, False

    async def close(self) -> None:
        self.is_closed_ = True
        self.context.pages.remove(self)


class FakePlaywrightBrowser:
    def __init__(self) -> None:
        self.contexts = [FakeContext("persisted", [{"name": "li_at", "value": "x"}], [ORIGIN])]
        self.made: list[tuple[FakeContext, dict]] = []

    async def new_context(self, **options: Any) -> FakeContext:
        state = options.get("storage_state") or {}
        context = FakeContext(f"made{len(self.made)}", state.get("cookies"), state.get("origins"))
        self.made.append((context, options))
        self.contexts.append(context)
        return context


# A site that keeps its sign in in local storage, as the persisted context holds it.
ORIGIN = {
    "origin": "https://jobs.example.org",
    "localStorage": [{"name": "auth", "value": "e2e-token-3c1f"}],
}


def chromium_of(slots: int = 2) -> chromium.Chromium:
    browser = chromium.Chromium(request("sweep"), "first", None, c.Config(slots=slots))
    browser.browser = FakePlaywrightBrowser()
    return browser


def test_the_first_lease_keeps_the_persisted_context_and_a_joiner_gets_its_own() -> None:
    async def go() -> None:
        browser = chromium_of()
        persisted = browser.browser.contexts[0]
        assert await browser.home("first", 0) is persisted
        own = await browser.home("second", 1)
        assert own is not persisted
        assert await browser.home("second", 1) is own
        state = {"cookies": [{"name": "li_at", "value": "x"}], "origins": [ORIGIN]}
        options = {"no_viewport": True, "accept_downloads": False, "storage_state": state}
        assert browser.browser.made == [(own, options)]
        # Signed in as the person is: the cookies, and the local storage of a site that keeps
        # its sign in there.
        assert own.jar == [{"name": "li_at", "value": "x"}]
        assert own.origins == [ORIGIN]
        page = FakePage(persisted)
        persisted.pages.append(page)
        assert browser.pages_of("second") == []
        assert browser.pages_of("first") == [page]

    run(go())


def test_a_storage_state_that_fails_still_gives_the_joiner_the_cookies() -> None:
    async def go() -> None:
        browser = chromium_of()
        browser.browser.contexts[0].state_fails = True
        own = await browser.home("second", 1)
        assert browser.browser.made == [(own, {"no_viewport": True, "accept_downloads": False})]
        assert (own.jar, own.origins) == ([{"name": "li_at", "value": "x"}], [])
        assert [event for event, _ in own.handlers] == ["page", "page"]  # guarded and placed

    run(go())


def test_a_joiner_s_cookies_go_back_to_the_profile_at_its_end() -> None:
    async def go() -> None:
        browser = chromium_of()
        own = await browser.home("second", 1)
        own.jar.append({"name": "session", "value": "new"})
        searched = await browser.new_context("second", 1, java_script_enabled=True)
        await browser.end_slot("second")
        assert own.closed
        assert searched.closed
        assert {"name": "session", "value": "new"} in browser.browser.contexts[0].jar
        assert browser.pages_of("second") == []

    run(go())


def test_the_persisted_context_is_never_given_to_a_later_lease() -> None:
    async def go() -> None:
        browser = chromium_of()
        persisted = browser.browser.contexts[0]
        persisted.pages.append(FakePage(persisted))
        await browser.end_slot("first")
        assert persisted.pages == []
        assert browser.persisted_by is None
        later = await browser.home("third", 0)
        assert later is not persisted  # its routes and listeners stay the first lease's

    run(go())


def test_one_browser_has_no_slot_view_and_the_tools_use_the_persisted_context() -> None:
    async def go() -> None:
        alone, shared = chromium_of(slots=1), chromium_of()

        class Held:
            lease_id = "second"
            request = replace_slot(request("apply_fill"), 1)

        assert alone.slot_of(Held()) is None
        view = shared.slot_of(Held())
        assert (view.lease_id, view.index) == ("second", 1)
        tab = actions.Tab(request=Held.request, state=actions.State(), browser=shared, pacer=None)
        assert await tab.home() is shared.browser.contexts[0]
        tab.slot = view
        assert await tab.home() is not shared.browser.contexts[0]

    run(go())


def replace_slot(made: Request, slot: int) -> Request:
    from dataclasses import replace

    return replace(made, slot=slot)


# --- the live view --------------------------------------------------------------------------------


class Writer:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data += data


def live_of(slots: int = 2) -> tuple[live.Live, Manager, Clock, list[Any]]:
    clock = Clock()
    mgr, _ = manager(clock, slots=slots)
    spawned: list[Any] = []

    async def spawn(*clip: str) -> live.Vnc:
        spawned.append(clip)
        raise OSError("no x11vnc in the unit tests")

    return live.Live(mgr, b"s", spawn=spawn), mgr, clock, spawned


@pytest.mark.parametrize("slots", [1, 2])
def test_each_view_shows_its_slot_s_region_only(slots: int) -> None:
    async def go() -> None:
        view, mgr, _, spawned = live_of(slots)
        first, _ = await mgr.acquire(request("sweep"))
        leases = [first]
        if slots == 2:
            second, _ = await mgr.acquire(request("apply_fill"))
            leases.append(second)
        for lease in leases:
            grant = live.Grant(lease.lease_id, VIEWER, 0, uuid.uuid4().hex)
            writer = Writer()
            await view.serve_viewer(grant, "k", {}, None, writer)  # type: ignore[arg-type]
            assert b"503" in writer.data
        expected = [()] if slots == 1 else [("1440x900+0+0",), ("1440x900+1440+0",)]
        assert spawned == expected

    run(go())


def test_a_take_over_of_one_slot_pauses_every_agent_and_tells_only_its_own() -> None:
    async def go() -> None:
        view, mgr, _, _ = live_of()
        first, _ = await mgr.acquire(request("sweep"))
        second, _ = await mgr.acquire(request("apply_fill"))
        view.take(first.lease_id, VIEWER)
        assert view.input_held()
        turn_a = asyncio.ensure_future(view.agent_turn(first.lease_id))
        turn_b = asyncio.ensure_future(view.agent_turn(second.lease_id))
        await asyncio.sleep(0)
        assert not turn_a.done()
        assert not turn_b.done()
        view.give_back(first.lease_id)
        assert await turn_a == live.PERSON_USED
        assert await turn_b is None  # the person never used this slot's window

    run(go())


def test_one_slot_holds_the_keyboard_at_a_time() -> None:
    async def go() -> None:
        view, mgr, _, _ = live_of()
        first, _ = await mgr.acquire(request("sweep"))
        second, _ = await mgr.acquire(request("apply_fill"))
        view.take(first.lease_id, VIEWER)
        other = "22222222-2222-4333-8444-555555555555"
        view.take(second.lease_id, other)
        assert view.holder(first.lease_id) is None
        assert view.holder(second.lease_id) == other
        assert first.state == "active"  # given back: its clock runs again
        assert second.wait_reason == "takeover"

    run(go())


def test_the_views_of_an_ended_slot_close_and_the_other_slot_s_stay() -> None:
    async def go() -> None:
        view, mgr, _, _ = live_of()
        first, _ = await mgr.acquire(request("sweep"))
        second, _ = await mgr.acquire(request("apply_fill"))
        writers = {lease.lease_id: Writer() for lease in (first, second)}

        class Closing:
            def __init__(self, sink: Writer) -> None:
                self.sink, self.closed = sink, False

            def write(self, data: bytes) -> None:
                self.sink.write(data)

            def close(self) -> None:
                self.closed = True

        made = {key: live.Viewer(key, VIEWER, Closing(w)) for key, w in writers.items()}  # type: ignore[arg-type]
        view.viewers.update(made.values())
        assert view.admit({live.GRANT_HEADER: grant_for(view, second.lease_id)}) is not None
        await mgr.release(first.lease_id, "released")
        view.tick()
        assert made[first.lease_id].closed
        assert not made[second.lease_id].closed
        assert view.admit({live.GRANT_HEADER: grant_for(view, first.lease_id)}) is None

    run(go())


def grant_for(view: live.Live, lease_id: str) -> str:
    exp = int(view.wall()) + 5
    return live.sign_grant(view.secret, lease_id, VIEWER, exp, uuid.uuid4().hex)
