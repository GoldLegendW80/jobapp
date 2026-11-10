"""Browser B23 (issue 780, a sweep of 2026-11-10): with one browser at once, the VIE website's
agent failed, Welcome to the Jungle then answered `browser_busy` without a search and Indeed never
started (`not_started`). browserd still ended the VIE website's lease (Chromium closing, then the
profile measured, within chromium.MEASURE_S of 120 s) after the api's own wait for that end
(client.END_TIMEOUT_S, 30 s) gave up, so it refused the next start `busy` while no website of the
sweep held a lease, and `interactive._website` took that refusal for a browser someone else
holds. Against a real Redis and browserd's own lease logic (b3_support), LinkedIn searched
through its public job search beside them: every website of the sweep is attempted, one at a
time; a browser the person's fill or another person holds is still `browser_busy`; a browser
that is never freed is `browser_busy` after FREEING_TRIES asks. No wall clock: the sleeps are
the test's, and browserd's end waits on an event the test sets.
"""

import asyncio
import dataclasses
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.browser import platform_policy, sources
from app.browser.client import BrowserdClient
from app.browser.sources import interactive, linkedin_guest
from app.jobs import sweep
from app.profile.schemas import SearchSection
from tests.browser.b3_support import BASE, FakeBrowserd, MemoryProfiles, World, flush, redis_url
from tests.browser.fakes import Known
from tests.browser.test_linkedin_guest import Allow, LinkedInFake

__all__ = ["redis_url"]

PERSON = "00000000-0000-4000-8000-0000000000e3"
OTHER = "00000000-0000-4000-8000-0000000000e4"
FILL = "7d3c2b1a-0f9e-4d8c-8b7a-6f5e4d3c2b1a"
FIXTURES = "http://fixtures.example.test"
VIE = "https://mon-vie-via.businessfrance.test/offres"
SITES = [
    {"platform": "custom", "url": VIE, "enabled": True, "mode": "llm_browser"},
    {"platform": "linkedin", "url": "https://www.linkedin.com/", "enabled": True,
     "mode": "llm_browser"},
    {"platform": "welcome_to_the_jungle", "url": "https://www.welcometothejungle.com/fr",
     "enabled": True, "mode": "llm_browser"},
    {"platform": "indeed", "url": "https://fr.indeed.com/", "enabled": True,
     "mode": "llm_browser"},
]  # fmt: skip
WEBSITES = [SITES[0], SITES[2], SITES[3]]


class OneBrowser(MemoryProfiles):
    """The person's browsers at once, as an admin set them (users.browsers_at_once): 1."""

    async def browsers(self, user_id: str) -> int:
        return 1


class SlowEnd:
    """browserd's end of a lease that outlives the api's wait for it: the DELETE goes on in
    browserd while the api's call times out, as client.END_TIMEOUT_S makes it; browserd's measure
    of the profile waits until `freed` is set. The api asking again later is answered as is."""

    def __init__(self, browserd: FakeBrowserd, measuring: asyncio.Event) -> None:
        self.browserd, self.measuring = browserd, measuring
        self.ends: list[asyncio.Task[httpx.Response]] = []
        self.timed_out: set[str] = set()

    async def ended(self) -> None:
        """Every end browserd went on with is over."""
        await asyncio.gather(*self.ends)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            request.method == "DELETE"
            and path.startswith("/leases/")
            and path not in self.timed_out
        ):
            self.timed_out.add(path)
            self.measuring.clear()
            end = asyncio.ensure_future(self.browserd.handle(request))
            self.ends.append(end)
            measuring = asyncio.ensure_future(self.measuring.wait())
            await asyncio.wait({end, measuring}, return_when=asyncio.FIRST_COMPLETED)
            measuring.cancel()
            raise httpx.ReadTimeout("timed out", request=request)
        return await self.browserd.handle(request)


@dataclasses.dataclass
class Sweep:
    world: World
    freed: asyncio.Event
    slow: SlowEnd
    searched: list[str] = dataclasses.field(default_factory=list)
    slept: list[float] = dataclasses.field(default_factory=list)
    most: int = 0  # the most leases browserd held during a search


@pytest.fixture
def world(redis_url: str) -> World:
    flush(redis_url)
    return World(redis_url)


def setup(world: World) -> Sweep:
    freed, measuring = asyncio.Event(), asyncio.Event()

    async def measure(_: Any) -> int:
        measuring.set()
        await freed.wait()
        return 1000

    world.browserd = FakeBrowserd(world.clock, world.secret, measure=measure)
    manager = world.browserd.manager
    manager.config = dataclasses.replace(manager.config, slots=2)  # the server's BROWSER_SLOTS
    slow = SlowEnd(world.browserd, measuring)
    client = BrowserdClient(BASE, world.secret, transport=httpx.MockTransport(slow.handle))
    world.store = world.make_store(client=client, profiles=OneBrowser())
    return Sweep(world, freed, slow)


def search_of() -> SearchSection:
    return SearchSection.model_validate(
        {"title_include": {"terms": ["Robotics Engineer"]}, "interactive_sites": SITES}
    )


async def run_sweep(
    state: Sweep, searched: Any, sleep: Any = None
) -> tuple[dict[str, dict[str, Any]], LinkedInFake]:
    """The sweep's browser hook (sources.Runner): LinkedIn through its public job search, which
    answers 429, then the websites read in the browser, each search a stand in."""
    search = search_of()
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-11-10T09:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    now = [0.0]
    linkedin = LinkedInFake(now, answers={1: (429, {})})

    async def slept(seconds: float) -> None:
        now[0] += seconds
        state.slept.append(seconds)
        if sleep is not None:
            await sleep(seconds)

    async def progress() -> None:
        pass

    runner = sources.Runner(
        state.world.store,
        lambda *_: object(),
        base_url=FIXTURES,
        sleep=slept,
        monotonic=lambda: now[0],
        pacer=sources.MemoryPacer(lambda: now[0]),
        finder=SimpleNamespace(available=_yes, profile=_nothing),
        interactive_policy=Allow(),
        guest_http=lambda: httpx.MockTransport(linkedin.handler),
        guest_cooldown=linkedin_guest.MemoryCooldown(lambda: now[0]),
    )
    plan = SimpleNamespace(
        search=search,
        user_id=PERSON,
        search_scope=scope,
        sweep_id=uuid.uuid4(),
        known=Known(),
        rules=None,
    )

    async def search_stand_in(agent: Any, pages: Any, scope: Any, *_: Any) -> str | None:
        state.most = max(state.most, len(state.world.browserd.manager.held()))
        state.searched.append(scope.start)
        return await searched(scope.start)

    interactive_search = interactive._search
    interactive._search = search_stand_in
    try:
        await runner(plan, stats, progress, 10_000.0)
    finally:
        interactive._search = interactive_search
        state.freed.set()
        await state.slow.ended()
        await state.world.close()
    return {entry["source_id"]: entry for entry in stats.sources}, linkedin


def start_of(site: dict[str, Any]) -> str:
    """Where the website's search starts: a protected platform's at the fixture host."""
    platform = site["platform"]
    return platform_policy.test_start(FIXTURES, platform, site["url"])


def source_id(site: dict[str, Any]) -> str:
    mode = linkedin_guest.MODE if site["platform"] == "linkedin" else "llm_browser"
    return sweep._source_id(mode, site["url"])


async def _yes(_: str) -> bool:
    return True


async def _nothing(_: str) -> str:
    return ""


def errors(entries: dict[str, dict[str, Any]]) -> list[Any]:
    return [entries[source_id(site)]["error"] for site in SITES]


def test_one_browser_four_sources_the_first_website_failing_every_website_is_attempted(
    world: World,
) -> None:
    state = setup(world)

    async def searched(start: str) -> str | None:
        return "agent_failed" if start == VIE else None

    async def sleep(seconds: float) -> None:
        # browserd ends the VIE website's lease while the next website asks again: it ends now.
        manager = state.world.browserd.manager
        if seconds == interactive.FREEING_POLL_S and manager.ending is not None:
            state.freed.set()
            await state.slow.ended()

    entries, linkedin = asyncio.run(run_sweep(state, searched, sleep))
    assert errors(entries) == ["agent_failed", "linkedin_rate_limited", None, None]
    assert [entries[source_id(site)]["outcome"] for site in WEBSITES] == [
        "failed",
        "no_matches",
        "no_matches",
    ]
    assert state.searched == [start_of(site) for site in WEBSITES]
    assert len(linkedin.requests) == 1
    # One at a time, as the person's one browser: three leases, never two at once.
    assert [r.purpose for r in state.world.browserd.starts] == ["sweep"] * 3
    assert state.most == 1
    assert interactive.FREEING_POLL_S in state.slept  # the browser was being freed: asked again
    assert state.world.browserd.manager.held() == []


def test_a_fill_that_takes_the_freed_browser_makes_the_next_website_busy(world: World) -> None:
    state = setup(world)
    fill: list[Any] = []

    async def searched(start: str) -> str | None:
        if start == VIE:
            # The person's application asks for the browser while the VIE website holds it.
            asked = await state.world.store.acquire(PERSON, "apply_fill", True, application_id=FILL)
            assert asked.waiting is not None
            return "agent_failed"
        return None

    async def sleep(seconds: float) -> None:
        state.freed.set()
        await state.slow.ended()
        if not fill:  # the fill's own job asks again first, and takes the browser
            fill.append(
                await state.world.store.acquire(PERSON, "apply_fill", True, application_id=FILL)
            )

    entries, _ = asyncio.run(run_sweep(state, searched, sleep))
    assert fill[0].lease is not None
    assert errors(entries) == [
        "agent_failed",
        "linkedin_rate_limited",
        "browser_busy",
        "not_started",
    ]
    assert state.searched == [start_of(SITES[0])]
    assert [entries[source_id(site)]["outcome"] for site in WEBSITES] == [
        "failed",
        "failed",
        "not_attempted",
    ]


def test_a_browser_another_person_holds_is_busy_at_once(world: World) -> None:
    state = setup(world)

    async def searched(start: str) -> str | None:
        return None

    async def go() -> tuple[dict[str, dict[str, Any]], Any]:
        held = await state.world.store.acquire(OTHER, "apply_fill", True, application_id=FILL)
        assert held.lease is not None
        return await run_sweep(state, searched)

    entries, _ = asyncio.run(go())
    assert errors(entries) == [
        "browser_busy",
        "linkedin_rate_limited",
        "not_started",
        "not_started",
    ]
    assert state.searched == []
    assert interactive.FREEING_POLL_S not in state.slept


def test_a_browser_never_freed_is_busy_after_its_tries(world: World) -> None:
    state = setup(world)

    async def searched(start: str) -> str | None:
        return "agent_failed" if start == VIE else None

    entries, _ = asyncio.run(run_sweep(state, searched))
    assert errors(entries) == [
        "agent_failed",
        "linkedin_rate_limited",
        "browser_busy",
        "not_started",
    ]
    polls = [s for s in state.slept if s == interactive.FREEING_POLL_S]
    assert polls == [interactive.FREEING_POLL_S] * interactive.FREEING_TRIES
    assert state.searched == [start_of(SITES[0])]
