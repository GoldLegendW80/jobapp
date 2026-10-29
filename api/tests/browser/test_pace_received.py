"""The pace per host as the website receives it (SPEC-browser: a pause of 5 to 15 s between two
pages of a host, counted from the end of the previous page; browser-j15 checks it on the fixture
website's log).

browserd waits through its own human pacing before it sends a page's request (browserd/tools:
a dwell of DWELL_S before every action, a hover of HOVER_S before a click or a typing, the keys
of a typed search, a reading pause after a navigation), so a pause counted between the starts of
the api's calls let the website see two pages 3.56 s apart (browser-j15, run 1 of the journeys
at d462ed42). `Pages` now counts the pause from the end of the previous page of the host, and
one page of a host runs at a time, across people.

The fake browserd below runs browserd's own pacing code (browserd/tools/pacing.py) on the test's
virtual clock with every draw at an end of its range; the website's log is the time each request
reaches it. No test sleeps: every wait is a step of the virtual clock.
"""

import asyncio
import heapq
import itertools
import random
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from redis.exceptions import RedisError

from app.browser import sources
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.sources import (
    HOLD_KEY,
    PACE_HOLD_S,
    PACE_KEY,
    PACE_POLL_S,
    PACE_TTL_S,
    PAGE_PAUSE_S,
    WEBSITE_CAP_S,
    MemoryPacer,
    Pages,
    RedisPacer,
    SourceError,
    linkedin,
)
from tests.browser.b3_support import flush, pool_of, redis_url

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd.tools import pacing  # noqa: E402

__all__ = ["redis_url"]

HOST = "platform:indeed"
URL = "https://www.indeed.com/jobs"
SNAPSHOT = '- heading "Jobs" [level=1] [ref=e1]'
# Both ranges the sweep uses: every website's, and LinkedIn's longer one (platform_policy).
RANGES = {"websites": PAGE_PAUSE_S, "linkedin": linkedin.PAUSE_S}


class Ends(random.Random):
    """A random source that answers an end of each range: `high` names the ranges drawn at
    their top, every other range is drawn at its bottom."""

    def __init__(self, high: tuple[tuple[float, float], ...] = ()) -> None:
        super().__init__(0)
        self.high = set(high)

    def uniform(self, a: float, b: float) -> float:
        return b if (a, b) in self.high else a


class VirtualTime:
    """A clock that moves only when every task waits on it: `sleep` parks a task until the
    clock reaches its time, and `run` moves the clock to the earliest parked time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.parked: list[tuple[float, int, asyncio.Future[None]]] = []
        self.order = itertools.count()

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self.parked, (self.now + max(0.0, seconds), next(self.order), future))
        await future

    def run(self, *work: Callable[[], Awaitable[Any]]) -> list[Any]:
        async def drive() -> list[Any]:
            tasks = [asyncio.ensure_future(job()) for job in work]
            while True:
                for _ in range(10_000):  # every running task reaches a park, or ends
                    if len(self.parked) >= sum(not task.done() for task in tasks):
                        break
                    await asyncio.sleep(0)
                else:
                    raise AssertionError("a task waits on something other than the clock")
                if all(task.done() for task in tasks):
                    return [task.result() for task in tasks]
                at, _, future = heapq.heappop(self.parked)
                self.now = max(self.now, at)
                future.set_result(None)

        return asyncio.run(drive())


class FakeBrowserd:
    """browserd's side of the pages of one lease, with its own pacing code on the virtual clock
    (browserd/tools/actions.py): the request reaches the website after the dwell (and the
    hover and keys), and before the call returns. Each action takes the next of `draws`: the
    ranges it draws at their top."""

    def __init__(
        self,
        time: VirtualTime,
        site: list[float],
        draws: list[tuple],
        who: list[str] | None = None,
        name: str = "",
    ) -> None:
        self.time, self.site, self.draws = time, site, list(draws)
        self.who, self.name = who, name
        self.calls: list[str] = []

    def _request(self) -> None:
        """The page's request reaches the website now."""
        self.site.append(self.time())
        if self.who is not None:
            self.who.append(self.name)

    def _pacer(self) -> pacing.Pacer:
        return pacing.Pacer(self.time.sleep, Ends(self.draws.pop(0) if self.draws else ()))

    async def navigate(self, url: str) -> dict[str, Any]:
        self.calls.append("navigate")
        pacer = self._pacer()
        await pacer.dwell()
        self._request()
        await pacer.read()  # after the page loaded, still within the call
        return {"ok": True, "url": url}

    async def click(self, ref: str, element: str = "") -> dict[str, Any]:
        self.calls.append("click")
        pacer = self._pacer()
        await pacer.dwell()  # Tab.approach: the dwell, the scroll, the hover, HOVER_S
        await pacer.hover()
        self._request()
        return {"ok": True, "url": URL}

    async def type(self, text: str) -> dict[str, Any]:
        """browser_type with `submit`: the Enter after the last key sends the search."""
        self.calls.append("type")
        pacer = self._pacer()
        await pacer.dwell()
        await pacer.hover()
        for delay in pacer.key_delays_s(len(text)):
            await pacer.sleep(delay)
        self._request()
        return {"ok": True, "url": URL}

    async def snapshot(self) -> dict[str, Any]:
        self.calls.append("snapshot")
        return {"ok": True, "snapshot": SNAPSHOT, "url": URL}


ACTIONS: dict[str, Callable[[FakeBrowserd], Callable[[], Awaitable[dict[str, Any]]]]] = {
    "navigate": lambda b: lambda: b.navigate(URL),
    "click": lambda b: lambda: b.click("e1", "Search"),
    "type": lambda b: lambda: b.type("engineer"),
}
# The ranges each action of browserd draws: every combination of their ends is a row.
DRAWS = {
    "navigate": (pacing.DWELL_S, pacing.READ_S),
    "click": (pacing.DWELL_S, pacing.HOVER_S),
    "type": (pacing.DWELL_S, pacing.HOVER_S, pacing.TYPE_MS),
}


def ends_of(kind: str) -> list[tuple]:
    """Every choice of ends for the ranges of one action: the ranges at their top."""
    ranges = DRAWS[kind]
    return [
        tuple(r for r, top in zip(ranges, tops, strict=True) if top)
        for tops in itertools.product((False, True), repeat=len(ranges))
    ]


def pages_of(
    browserd: FakeBrowserd,
    time: VirtualTime,
    pacer: Any,
    pause: tuple[float, float],
    high: bool = False,
    **kw: Any,
) -> Pages:
    return Pages(
        browserd,
        kw.pop("deadline", 1e9),
        sleep=time.sleep,
        rng=Ends((pause,) if high else ()),
        monotonic=time,
        host=HOST,
        pacer=pacer,
        pause=pause,
        **kw,
    )


class StartPacer:
    """The rule until 2026-10-29: the pause counted from the start of the previous page."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock, self.last = clock, -1e18

    async def reserve(self, host: str, pause: float, holder: str) -> float:
        now = self.clock()
        self.last = max(now, self.last + pause)
        return self.last - now

    async def release(self, host: str, holder: str) -> None:
        return None


def two_pages(first: str, second: str, draws: list[tuple], pause, high, pacer_of) -> float:
    """The website's gap between two pages of one lease, browserd drawing `draws`."""
    time, site = VirtualTime(), []
    browserd = FakeBrowserd(time, site, draws)
    pages = pages_of(browserd, time, pacer_of(time), pause, high)

    async def go() -> None:
        await pages._act(ACTIONS[first](browserd))
        await pages._act(ACTIONS[second](browserd))

    time.run(go)
    assert len(site) == 2, site
    return site[1] - site[0]


@pytest.mark.parametrize("platform", sorted(RANGES))
@pytest.mark.parametrize("high", [False, True], ids=["shortest", "longest"])
@pytest.mark.parametrize(("first", "second"), list(itertools.product(sorted(ACTIONS), repeat=2)))
def test_the_website_receives_two_pages_at_least_the_pause_apart(platform, high, first, second):
    """Every end of every range browserd draws, for both actions: the website's gap is at
    least the pause the api drew (and browserd's shortest dwell on top)."""
    pause = RANGES[platform]
    drawn = pause[1] if high else pause[0]
    short = []
    for a, b in itertools.product(ends_of(first), ends_of(second)):
        gap = two_pages(first, second, [a, b], pause, high, MemoryPacer)
        if gap < drawn + pacing.DWELL_S[0] - 1e-9:
            short.append((a, b, gap))
    assert not short, (drawn, short)


@pytest.mark.parametrize("platform", sorted(RANGES))
def test_the_rule_counted_from_the_starts_is_red_on_the_same_table(platform):
    """The old rule in the same harness: a click whose dwell and hover were the longest, then a
    navigation with the shortest dwell, reach the website 2.6 s closer than the pause."""
    pause = RANGES[platform]
    slow_click = (pacing.DWELL_S, pacing.HOVER_S)
    gap = two_pages("click", "navigate", [slow_click, ()], pause, False, StartPacer)
    worst = pause[0] - (pacing.DWELL_S[1] + pacing.HOVER_S[1]) + pacing.DWELL_S[0]
    assert gap == pytest.approx(worst)
    assert gap < pause[0]
    new = two_pages("click", "navigate", [slow_click, ()], pause, False, MemoryPacer)
    assert new == pytest.approx(pause[0] + pacing.DWELL_S[0])


THINK_S = 2.0  # the finder's model turn between two pages of its lease


def people(pacer_of, draws_a, draws_b, count: int = 3) -> tuple[list[float], list[str]]:
    """Two people's leases on one host at once, each opening `count` pages with a model turn
    between them: the website's log of every request, and whose each was."""
    time, site, who = VirtualTime(), [], []
    pacer = pacer_of(time)

    def person(name: str, draws: list[tuple]) -> Callable[[], Awaitable[None]]:
        browserd = FakeBrowserd(time, site, draws, who, name)
        pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S)

        async def go() -> None:
            for index in range(count):
                await pages._act(ACTIONS["click" if index % 2 else "navigate"](browserd))
                await time.sleep(THINK_S)

        return go

    time.run(person("a", draws_a), person("b", draws_b))
    return site, who


@pytest.mark.parametrize(
    ("draws_a", "draws_b"),
    [
        ([(pacing.DWELL_S, pacing.READ_S)] * 3, [()] * 3),
        ([()] * 3, [(pacing.DWELL_S, pacing.HOVER_S)] * 3),
        ([(pacing.DWELL_S,), (), (pacing.HOVER_S,)], [(), (pacing.DWELL_S, pacing.HOVER_S), ()]),
    ],
)
def test_two_people_on_one_host_reach_the_website_the_pause_apart(draws_a, draws_b):
    """Across people (SPEC-browser: every person shares the home address): a page waits for the
    other person's page of the host to end, so the pages of both reach the website the pause
    apart; the rule counted from the starts lets them come closer."""
    site, who = people(MemoryPacer, draws_a, draws_b)
    order = "".join(who)
    assert len(site) == 6, (site, who)
    assert "ab" in order, who
    assert "ba" in order, who
    gaps = [b - a for a, b in itertools.pairwise(site)]
    assert min(gaps) >= PAGE_PAUSE_S[0] + pacing.DWELL_S[0] - 1e-9, gaps
    old, _ = people(StartPacer, draws_a, draws_b)
    assert min(b - a for a, b in itertools.pairwise(sorted(old))) < PAGE_PAUSE_S[0]


def test_a_page_waits_while_another_holds_its_host_and_ends_at_its_budget():
    """Nothing reaches browserd while another page holds the host: the page asks again every
    PACE_POLL_S, and ends at the sweep's time limit when the host never comes back."""
    time, site = VirtualTime(), []
    pacer = MemoryPacer(time)
    browserd = FakeBrowserd(time, site, [])
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S, deadline=200.0)
    asked = []
    reserve = pacer.reserve

    async def counted(host: str, pause: float, holder: str) -> float | None:
        asked.append(time())
        return await reserve(host, pause, holder)

    pacer.reserve = counted  # type: ignore[method-assign]

    async def go() -> str:
        assert await pacer.reserve(HOST, 5.0, "another person's page") == 0.0
        with pytest.raises(SourceError) as ended:
            await pages._act(ACTIONS["navigate"](browserd))
        return ended.value.code

    assert time.run(go) == ["time_budget"]
    assert browserd.calls == []
    assert site == []
    assert all(b - a == pytest.approx(PACE_POLL_S) for a, b in itertools.pairwise(asked[1:]))
    assert time() == pytest.approx(200.0 - sources.PAGE_BUDGET_S)


def test_a_page_that_cannot_fit_the_lease_while_it_waits_ends_lease_budget():
    time, site = VirtualTime(), []
    pacer = MemoryPacer(time)
    browserd = FakeBrowserd(time, site, [])
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S, lease_until=100.0)

    async def go() -> str:
        await pacer.reserve(HOST, 5.0, "another person's page")
        with pytest.raises(SourceError) as ended:
            await pages._act(ACTIONS["navigate"](browserd))
        return ended.value.code

    assert time.run(go) == ["lease_budget"]
    assert browserd.calls == []
    assert time() + PAGE_PAUSE_S[0] + sources.PAGE_LOAD_S > 100.0


def test_a_page_waits_for_another_only_while_its_own_lease_would_not_end_idle():
    """A page that outlives its lease (a preempted sweep's, in its action or a person's takeover)
    can hold the host for minutes: the waiting page gives up its website (`lease_budget`) before
    browserd ends its lease after the lease's idle time without a tool call (`lease_ended` would
    end the whole sweep), counted from the end of its own last page."""
    time, site = VirtualTime(), []
    pacer = MemoryPacer(time)
    browserd = FakeBrowserd(time, site, [])
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S, idle_s=300, deadline=1000.0)

    async def go() -> str:
        await pages._act(ACTIONS["navigate"](browserd))
        assert await pacer.reserve(HOST, 5.0, "a taken over page") == pytest.approx(5.0)
        with pytest.raises(SourceError) as ended:
            await pages._act(ACTIONS["navigate"](browserd))
        return ended.value.code

    first_end = pacing.DWELL_S[0] + pacing.READ_S[0]
    assert time.run(go) == ["lease_budget"]
    assert browserd.calls == ["navigate", "snapshot"]
    assert len(site) == 1
    # It asked until its next page could no longer start and load within the idle time.
    assert time() + PAGE_PAUSE_S[0] + sources.PAGE_LOAD_S > first_end + 300
    assert time() - PACE_POLL_S + PAGE_PAUSE_S[0] + sources.PAGE_LOAD_S <= first_end + 300


class Failing(FakeBrowserd):
    def __init__(self, time: VirtualTime, site: list[float], how: str) -> None:
        super().__init__(time, site, [])
        self.how = how

    async def navigate(self, url: str) -> dict[str, Any]:
        await super().navigate(url)
        if self.how == "ended":
            raise BrowserError("ended")
        if self.how == "unavailable":
            raise BrowserError("unavailable")
        if self.how == "cancelled":
            raise asyncio.CancelledError
        return {"ok": False, "error": "navigation_failed"}


@pytest.mark.parametrize(
    ("how", "code"),
    [
        ("failed", "page_failed"),
        ("ended", "lease_ended"),
        ("unavailable", "page_failed"),
        ("cancelled", None),
    ],
)
def test_a_page_that_fails_gives_its_host_back_and_the_pause_counts_from_its_end(how, code):
    time, site = VirtualTime(), []
    pacer = MemoryPacer(time)
    browserd = Failing(time, site, how)
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S)

    async def go() -> float:
        with pytest.raises(asyncio.CancelledError if code is None else SourceError) as raised:
            await pages._act(ACTIONS["navigate"](browserd))
        if code is not None:
            assert raised.value.code == code
        ended = time()
        assert pacer.holders == {}
        assert pacer.last[HOST] == ended
        assert await pacer.reserve(HOST, 5.0, "next") == pytest.approx(5.0)
        return ended

    [ended] = time.run(go)
    assert ended == pytest.approx(pacing.DWELL_S[0] + pacing.READ_S[0])


def test_a_page_cut_in_its_wait_gives_back_the_host_and_keeps_its_slot():
    """The sweep's time ran out while the page waited for its slot (`asyncio.timeout`): the
    host is free at once, and the slot it took stays the host's last page time."""
    time = VirtualTime()
    pacer = MemoryPacer(time)

    async def cut(seconds: float) -> None:
        raise asyncio.CancelledError

    pages = Pages(
        FakeBrowserd(time, [], []),
        1e9,
        sleep=cut,
        rng=Ends(),
        monotonic=time,
        host=HOST,
        pacer=pacer,
    )

    async def go() -> None:
        await pacer.reserve(HOST, 5.0, "earlier")
        await pacer.release(HOST, "earlier")
        with pytest.raises(asyncio.CancelledError):
            await pages._act(lambda: pages.tools.navigate(URL))

    time.run(go)
    assert pacer.holders == {}
    assert pacer.last[HOST] == PAGE_PAUSE_S[0]


class Unavailable(MemoryPacer):
    async def release(self, host: str, holder: str) -> None:
        raise StoreUnavailableError


def test_a_release_redis_refuses_never_hides_the_page_s_own_error():
    time = VirtualTime()
    browserd = Failing(time, [], "ended")
    pages = pages_of(browserd, time, Unavailable(time), PAGE_PAUSE_S)

    async def go() -> str:
        with pytest.raises(SourceError) as raised:
            await pages._act(ACTIONS["navigate"](browserd))
        return raised.value.code

    assert time.run(go) == ["lease_ended"]


def test_a_page_whose_release_redis_refuses_ends_the_website_unavailable():
    """No pace store, no next page: the error interactive.run_selected ends a website with
    `browser_unavailable` on."""
    time = VirtualTime()
    browserd = FakeBrowserd(time, [], [])
    pages = pages_of(browserd, time, Unavailable(time), PAGE_PAUSE_S)

    async def go() -> None:
        with pytest.raises(StoreUnavailableError):
            await pages._act(ACTIONS["navigate"](browserd))

    time.run(go)
    assert browserd.calls == ["navigate", "snapshot"]


# --- the rule of both pacers, the one in memory and the one in Redis ----------------------------


@pytest.fixture(params=["memory", "redis"])
def store(request) -> str | None:
    """The pacer under test: in memory, or in a real Redis (its database flushed)."""
    if request.param == "memory":
        return None
    url = request.getfixturevalue("redis_url")
    flush(url)
    return url


def test_both_pacers_count_the_pause_from_the_end_and_hold_the_host(store):
    clock = [1000.0]

    async def go() -> None:
        if store is None:
            await steps(MemoryPacer(lambda: clock[0]), None)
            return
        pool, close = pool_of(store)
        try:
            await steps(RedisPacer(pool, lambda: clock[0]), await pool())
        finally:
            await close()

    async def steps(pacer: Any, r: Any) -> None:
        assert await pacer.reserve(HOST, 5.0, "a1") == 0.0  # an idle host opens at once
        clock[0] += 1.0
        assert await pacer.reserve(HOST, 5.0, "b1") is None  # a's page has not ended
        assert await pacer.reserve("other.example.org", 5.0, "b1") == 0.0  # another host
        clock[0] += 2.6
        await pacer.release(HOST, "a1")  # a's page ended at 1003.6
        clock[0] += 0.4
        assert await pacer.reserve(HOST, 5.0, "b1") == pytest.approx(4.6)
        clock[0] += 4.6
        assert await pacer.reserve(HOST, 5.0, "a2") is None
        clock[0] += 3.0
        await pacer.release(HOST, "a1")  # not the holder: b's hold stays, its end moves on
        assert await pacer.reserve(HOST, 5.0, "a2") is None
        await pacer.release(HOST, "b1")
        assert await pacer.reserve(HOST, 30.0, "a2") == pytest.approx(30.0)
        # Cut before its slot came: the slot it took stays the host's last page time.
        await pacer.release(HOST, "a2")
        assert await pacer.reserve(HOST, 5.0, "b2") == pytest.approx(35.0)
        if r is not None:
            assert PACE_HOLD_S - 2 <= await r.ttl(HOLD_KEY + HOST) <= PACE_HOLD_S
            assert 0 < await r.ttl(PACE_KEY + HOST) <= PACE_TTL_S
            await pacer.release(HOST, "b2")
            assert await r.get(HOLD_KEY + HOST) is None

    asyncio.run(go())


def test_a_hold_is_bounded_by_the_website_s_cap_and_a_margin():
    """A live worker's page is cancelled at the website's cap at the latest (interactive._search,
    its wait included) and gives the host back: the hold outlives that cap by a margin only, so
    a leaked one costs the platform's other pages about 17 minutes, not 36."""
    assert WEBSITE_CAP_S == 900
    assert WEBSITE_CAP_S < PACE_HOLD_S <= WEBSITE_CAP_S + 120
    assert PACE_HOLD_S < PACE_TTL_S


@pytest.mark.parametrize("error", [RedisError("down"), OSError("down"), TimeoutError()])
def test_the_redis_pacer_without_redis_is_store_unavailable(error):
    async def pool() -> Any:
        raise error

    pacer = RedisPacer(pool)

    async def go() -> None:
        with pytest.raises(StoreUnavailableError):
            await pacer.reserve(HOST, 5.0, "a")
        with pytest.raises(StoreUnavailableError):
            await pacer.release(HOST, "a")

    asyncio.run(go())


# --- no hold is left behind ---------------------------------------------------------------------

CLOCK = 1000.0  # a clock that stands still, for the tests that run on a pacer without sleeping


class Quick:
    """Tools whose pages end at once (no browserd pacing): for the tests of the hold itself."""

    async def navigate(self, url: str) -> dict[str, Any]:
        return {"ok": True, "url": url}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "snapshot": SNAPSHOT, "url": URL}


async def no_sleep(seconds: float) -> None:
    return None


def run_on(store: str | None, body: Callable[[Any], Awaitable[None]]) -> None:
    """Runs `body(pacer)` on the pacer under test, the clock standing still at CLOCK."""

    async def go() -> None:
        if store is None:
            await body(MemoryPacer(lambda: CLOCK))
            return
        pool, close = pool_of(store)
        try:
            await body(RedisPacer(pool, lambda: CLOCK))
        finally:
            await close()

    asyncio.run(go())


class Cutting:
    """A pacer whose first `where` call (reserve or release) is cut by a cancel of the page's task:
    after the store did its work, when its reply is lost (`after`), else before it did anything."""

    def __init__(self, inner: Any, where: str, after: bool) -> None:
        self.inner, self.where, self.after, self.cut = inner, where, after, False

    async def _call(self, name: str, host: str, *args: Any) -> Any:
        method = getattr(self.inner, name)
        if name != self.where or self.cut:
            return await method(host, *args)
        self.cut = True
        if self.after:
            await method(host, *args)
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)  # the cancel lands here
        raise AssertionError("the cancel did not land")

    async def reserve(self, host: str, pause: float, holder: str) -> float | None:
        return await self._call("reserve", host, pause, holder)

    async def release(self, host: str, holder: str) -> None:
        await self._call("release", host, holder)


@pytest.mark.parametrize("after", [False, True], ids=["before the store", "in the store's reply"])
@pytest.mark.parametrize("where", ["reserve", "release"])
def test_a_cancel_in_the_store_s_round_trip_leaves_no_hold(store, where, after):
    """The person's Stop, the runtime's wall time or a SIGTERM can land inside the call that takes
    the hold (the store took it, the page never heard) or the one that gives it back (the success
    path): the page still gives the host back, on both pacers."""

    async def body(pacer: Any) -> None:
        cutting = Cutting(pacer, where, after)
        pages = Pages(
            Quick(),
            1e9,
            sleep=no_sleep,
            rng=Ends(),
            monotonic=lambda: CLOCK,
            host=HOST,
            pacer=cutting,
        )
        with pytest.raises(asyncio.CancelledError):
            await pages._act(lambda: pages.tools.navigate(URL))
        assert cutting.cut
        # Nobody holds the host: another page takes it at once.
        assert await pacer.reserve(HOST, 5.0, "another page") is not None

    run_on(store, body)


class Refusing(MemoryPacer):
    """A pacer whose release the store refuses the first `times` times, whoever asks."""

    def __init__(self, clock: Callable[[], float], times: int) -> None:
        super().__init__(clock)
        self.times = times
        self.released: list[str] = []

    async def release(self, host: str, holder: str) -> None:
        if self.times:
            self.times -= 1
            raise StoreUnavailableError
        self.released.append(holder)
        await super().release(host, holder)


def test_a_release_the_store_refuses_once_is_asked_again_on_an_error_path():
    time, site = VirtualTime(), []
    pacer = Refusing(time, 1)
    browserd = Failing(time, site, "failed")
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S)

    async def go() -> None:
        with pytest.raises(SourceError) as raised:
            await pages._act(ACTIONS["navigate"](browserd))
        assert raised.value.code == "page_failed"

    time.run(go)
    assert pacer.holders == {}
    assert len(pacer.released) == 1
    assert pages.unreleased == set()


def test_a_release_the_store_refuses_once_is_asked_again_on_the_success_path():
    time, site = VirtualTime(), []
    pacer = Refusing(time, 1)
    browserd = FakeBrowserd(time, site, [])
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S)

    async def go() -> None:
        await pages._act(ACTIONS["navigate"](browserd))

    time.run(go)
    assert pacer.holders == {}
    assert pages.unreleased == set()


def test_the_next_page_of_a_lease_never_waits_on_its_own_earlier_hold():
    """The store refused the release of a page that failed, and the retry too: the hold stays for
    PACE_HOLD_S. The same lease's next page gives it back before it asks, and its pause counts
    from then. (Without it the page asks every second until `lease_budget`.)"""
    time, site = VirtualTime(), []
    pacer = Refusing(time, 2)
    browserd = Failing(time, site, "failed")
    pages = pages_of(browserd, time, pacer, PAGE_PAUSE_S, lease_until=300.0)

    async def go() -> None:
        with pytest.raises(SourceError) as raised:
            await pages._act(ACTIONS["navigate"](browserd))
        assert raised.value.code == "page_failed"
        [left] = pages.unreleased
        assert pacer.holders == {HOST: left}  # the store still holds it
        await pages._act(ACTIONS["click"](browserd))

    time.run(go)
    assert pacer.holders == {}
    assert pages.unreleased == set()
    assert len(site) == 2
    assert site[1] - site[0] >= PAGE_PAUSE_S[0] + pacing.DWELL_S[0] - 1e-9


def test_a_hold_left_by_a_lease_is_not_given_back_for_another_person_s_page():
    """The left holder is this lease's: another lease's page, with its own `Pages`, still waits
    for it (the hold ends after PACE_HOLD_S)."""
    time, site = VirtualTime(), []
    pacer = Refusing(time, 2)
    first = Failing(time, site, "failed")
    left = pages_of(first, time, pacer, PAGE_PAUSE_S)
    other_browserd = FakeBrowserd(time, site, [])
    other = pages_of(other_browserd, time, pacer, PAGE_PAUSE_S, lease_until=100.0)

    async def go() -> str:
        with pytest.raises(SourceError):
            await left._act(ACTIONS["navigate"](first))
        with pytest.raises(SourceError) as ended:
            await other._act(ACTIONS["navigate"](other_browserd))
        return ended.value.code

    assert time.run(go) == ["lease_budget"]
    assert other_browserd.calls == []
    assert len(pacer.holders) == 1


def test_two_concurrent_calls_of_one_lease_still_pass_the_host_one_at_a_time():
    """One holder per page, not per lease (concurrent tool calls of one finder session): the
    second call waits for the end of the first, so the website sees them the pause apart."""
    time, site = VirtualTime(), []
    browserd = FakeBrowserd(time, site, [])
    pages = pages_of(browserd, time, MemoryPacer(time), PAGE_PAUSE_S)

    time.run(
        lambda: pages._act(ACTIONS["navigate"](browserd)),
        lambda: pages._act(ACTIONS["click"](browserd)),
    )
    assert len(site) == 2
    assert site[1] - site[0] >= PAGE_PAUSE_S[0] + pacing.DWELL_S[0] - 1e-9
