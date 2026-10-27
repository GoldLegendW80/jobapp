"""One native search per title and place combination on a selected website (owner, 2026-10-26).

Each combination runs on its own lease with its own frozen scope, inside the website's shared
page cap, token budget, pacing and the platform's caps; the postings merge into one result.
"""

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.browser import finder, platform_policy, sources
from app.browser.sources import interactive
from app.browser.sources.interactive import PLAIN, SEARCHES_MAX, searches_for
from app.profile.schemas import SearchSection
from tests.browser.test_b9_site import ScriptedFinder
from tests.browser.test_b13_runtime import BASE, FIRST
from tests.browser.test_selected_interactive import CANARY, Policy, execute, selected

LINKEDIN = "https://www.linkedin.com/jobs/"


@dataclass
class SearchSite:
    """A native search website: Search lists the postings of the words typed, by their slug."""

    base: str = BASE
    results: dict[str, list[str]] = field(default_factory=dict)  # typed words: posting titles
    scopes: list[dict[str, Any]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    current: str = ""
    typed: str = ""
    pages: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def slug(title: str) -> str:
        return title.lower().replace(" ", "-")

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        self.scopes.append(args.pop("_discovery_scope"))
        self.actions.append(name)
        if name == "browser_navigate":
            url = args["url"]
            if url == self.base:
                self.pages[url] = FIRST
            if url not in self.pages:
                return {"ok": False, "error": "navigation_failed"}
            self.current = url
        elif name == "browser_type":
            self.typed = args["text"]
        elif name == "browser_click":
            titles = self.results.get(self.typed, [])
            self.pages[self.current] = (
                "\n".join(
                    f'- link "{title}" [ref=e{9 + n}]\n  - /url: jobs/{self.slug(title)}'
                    for n, title in enumerate(titles)
                )
                or "- paragraph: No results"
            )
            for title in titles:
                self.pages[f"{self.base}jobs/{self.slug(title)}"] = (
                    f'- heading "{title}"\n- paragraph: What the {title} does.'
                )
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": self.current, "snapshot": self.pages[self.current]}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def searching(site: SearchSite, tokens: int = 1000, log: list[Any] | None = None):
    """The model: types its scope's words, presses Search, opens and reports every result."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        if log is not None:
            log.append((session.interactive, session.token_budget))
        query = session.interactive.query
        ran = finder.Ran(input_tokens=tokens, output_tokens=10)
        if (await session.interact("browser_type", {"ref": "e1", "text": query}))[1]:
            return ran  # told to stop, as a model is
        if (await session.interact("browser_click", {"ref": "e3"}))[1]:
            return ran
        for title in site.results.get(query, []):
            url = f"{site.base}jobs/{site.slug(title)}"
            if (await session.navigate(url))[1]:
                break
            session.report([{"title": title, "location": "Paris", "url": url, "text": "x"}])
        return ran

    return script


def run(search: SearchSection, site: SearchSite, agent: Any, **kw: Any):
    entries, stored, leases, _, _ = asyncio.run(
        execute(search, "e2e-person", agent, tools=site, deadline=100_000, **kw)
    )
    [entry] = entries
    return entry, stored, leases


def test_several_titles_run_one_search_each_in_saved_order_on_their_own_lease():
    site = SearchSite(results={"robot": ["Robot Engineer"], "drone": ["Drone Pilot"]})
    seen: list[Any] = []
    entry, stored, leases = run(
        selected(title_include={"terms": ["robot", "drone"]}),
        site,
        ScriptedFinder(searching(site, log=seen)),
    )
    assert [scope.query for scope, _ in seen] == ["robot", "drone"]
    assert leases.released == ["lease-1", "lease-2"]
    assert (entry["searches"], entry["searches_planned"], entry["searches_total"]) == (2, 2, 2)
    assert (entry["found"], entry["error"], entry["outcome"]) == (
        2,
        "finding_persistence_pending",
        "partial",
    )
    assert entry["result_pages"] == 8  # start, typed, searched and one posting, twice
    assert entry["tokens"] == {"input": 2000, "output": 20}
    assert stored == []


def test_several_places_combine_with_each_title_titles_first():
    site = SearchSite()
    seen: list[Any] = []
    entry, _, leases = run(
        selected(
            title_include={"terms": ["robot", "drone"]}, location={"terms": ["Paris", "Lyon"]}
        ),
        site,
        ScriptedFinder(searching(site, log=seen)),
    )
    assert [(s.query, dict(s.filters)["location"]) for s, _ in seen] == [
        ("robot", "Paris"),
        ("robot", "Lyon"),
        ("drone", "Paris"),
        ("drone", "Lyon"),
    ]
    assert all(dict(s.filters)["remote"] == "remote" for s, _ in seen)
    assert len(leases.released) == 4
    assert (entry["searches"], entry["found"], entry["error"]) == (4, 0, None)
    assert entry["outcome"] == "no_matches"


def test_the_cap_runs_the_first_combinations_and_counts_the_rest():
    site = SearchSite()
    seen: list[Any] = []
    titles, places = ["robot", "drone", "rover"], ["Paris", "Lyon", "Nantes"]
    entry, _, leases = run(
        selected(title_include={"terms": titles}, location={"terms": places}),
        site,
        ScriptedFinder(searching(site, log=seen)),
    )
    assert SEARCHES_MAX == 6
    expected = [(t, p) for t in titles for p in places][:SEARCHES_MAX]
    assert [(s.query, dict(s.filters)["location"]) for s, _ in seen] == expected
    assert len(leases.acquired) == SEARCHES_MAX
    assert (entry["searches"], entry["searches_planned"], entry["searches_total"]) == (6, 6, 9)


def test_duplicate_postings_across_combinations_count_once():
    site = SearchSite(results={"robot": ["Robot Engineer", "Fleet Lead"], "drone": ["Fleet Lead"]})
    entry, _, _ = run(
        selected(title_include={"terms": ["robot", "drone"]}),
        site,
        ScriptedFinder(searching(site)),
    )
    assert (entry["searches"], entry["found"]) == (2, 2)


def test_the_website_page_cap_is_shared_by_its_searches():
    site = SearchSite(results={t: [f"{t} Engineer"] for t in ("a1", "b2", "c3", "d4", "e5", "f6")})
    entry, _, leases = run(
        selected(title_include={"terms": ["a1", "b2", "c3", "d4", "e5", "f6"]}),
        site,
        ScriptedFinder(searching(site)),
    )
    assert finder.PAGES_MAX == 20
    assert entry["result_pages"] == finder.PAGES_MAX  # 5 searches of 4 pages each
    assert (entry["searches"], entry["error"], entry["outcome"]) == (5, "page_budget", "partial")
    assert entry["found"] == 5
    assert len(leases.acquired) == 5  # the sixth never asked for the browser


def test_the_token_budget_is_shared_by_the_searches_of_a_website():
    site = SearchSite()
    seen: list[Any] = []
    entry, _, leases = run(
        selected(title_include={"terms": ["robot", "drone", "rover"]}),
        site,
        ScriptedFinder(searching(site, tokens=400_000, log=seen)),
    )
    assert [budget for _, budget in seen] == [finder.TOKEN_BUDGET, finder.TOKEN_BUDGET - 400_000]
    assert (entry["searches"], entry["error"]) == (2, "token_budget")
    assert len(leases.acquired) == 2


def busy(calls: int, sessions: list[finder.FinderSession], turns: int = 0):
    """The model: types its words, presses Search, then reads the page again until it made
    `calls` tool calls, or until a tool tells it to stop; the runtime counts `turns` (0: none)."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        sessions.append(session)
        query = session.interactive.query
        steps = [
            lambda: session.interact("browser_type", {"ref": "e1", "text": query}),
            lambda: session.interact("browser_click", {"ref": "e3"}),
            *[session.snapshot] * (calls - 2),
        ]
        for step in steps:
            if (await step())[0].startswith("Stopped:"):
                break  # told to stop, as a model is
        return finder.Ran(input_tokens=10, output_tokens=1, turns=turns)

    return script


@pytest.mark.parametrize(
    ("titles", "calls", "budgets"),
    [
        # Six searches of 9 calls: 4 of them use 36 turns, the fifth stops at 4 more.
        (["a1", "b2", "c3", "d4", "e5", "f6"], 9, [40, 31, 22, 13, 4]),
        # The reviewer's probe: 30 calls a search, the second stops at 40 in all.
        (["a1", "b2", "c3"], 30, [40, 10]),
    ],
)
def test_the_website_turn_cap_is_shared_by_its_searches(titles, calls, budgets):
    """SPEC-browser's 40 model turns hold for the website, across all its native searches."""
    sessions: list[finder.FinderSession] = []
    entry, _, leases = run(
        selected(title_include={"terms": titles}),
        SearchSite(),
        ScriptedFinder(busy(calls, sessions)),
    )
    assert finder.MAX_TURNS == 40
    assert [s.turn_budget for s in sessions] == budgets
    assert [s.turns for s in sessions] == [*[calls] * (len(budgets) - 1), budgets[-1]]
    assert sum(s.turns for s in sessions) == finder.MAX_TURNS
    assert sessions[-1].stopped == "turn_limit"
    assert (entry["searches"], entry["error"]) == (len(budgets), "turn_limit")
    assert len(leases.acquired) == len(budgets)  # the later combinations never asked


def test_the_runtime_s_turn_count_wins_when_it_is_higher_and_its_stop_ends_the_website():
    """A turn the tools never see (text only, a refused tool) is counted by the runtime."""
    sessions: list[finder.FinderSession] = []
    entry, _, leases = run(
        selected(title_include={"terms": ["a1", "b2", "c3"]}),
        SearchSite(),
        ScriptedFinder(busy(3, sessions, turns=25)),
    )
    assert [s.turn_budget for s in sessions] == [40, 15]
    assert (entry["searches"], entry["error"], len(leases.acquired)) == (2, "turn_limit", 2)

    async def stopped(session: finder.FinderSession) -> finder.Ran:
        sessions.append(session)
        await busy(3, [])(session)
        return finder.Ran(stopped="turn_limit", turns=4)  # the runtime cut the run

    sessions.clear()
    entry, _, leases = run(
        selected(title_include={"terms": ["a1", "b2"]}), SearchSite(), ScriptedFinder(stopped)
    )
    assert (len(sessions), entry["error"], len(leases.acquired)) == (1, "turn_limit", 1)


def test_each_tool_call_is_a_turn_and_none_runs_past_the_budget():
    site = SearchSite(results={"robot": ["Robot Engineer"]}, current=BASE, pages={BASE: FIRST})
    pages = sources.Pages(
        site,
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.Random(1),  # noqa: S311  deterministic test pacing
        monotonic=lambda: 0,
    )
    scope = finder.InteractiveScope(BASE, "robot", ())
    session = finder.FinderSession(pages, BASE, FIRST, "e2e-person", interactive=scope)
    assert session.turn_budget is None  # a career site: the runtime's 40 turns alone
    session.turn_budget = 6
    posting = BASE + "jobs/robot-engineer"

    async def model() -> list[tuple[str, bool]]:
        return [
            await session.interact("browser_type", {"ref": "e1", "text": "robot"}),
            await session.interact("browser_click", {"ref": "e3"}),
            await session.navigate(posting),
            session.report([{"title": "Robot Engineer", "url": posting, "text": "x"}]),
            await session.wait_for(None, 1),
            await session.snapshot(),
            await session.snapshot(),  # the seventh
            await session.navigate(posting),
            await session.report_verified([]),
        ]

    answers = asyncio.run(model())
    assert all(not error for _, error in answers[:6]), answers
    stop = "Stopped: turn_limit. Report what you found, then stop."
    assert answers[6:] == [(stop, True)] * 3
    assert (session.turns, session.stopped, len(session.found)) == (6, "turn_limit", 1)
    assert site.actions == ["browser_type", "browser_click", "browser_navigate"]

    # One verified report is one turn, never two.
    saved: list[finder.VerifiedBatch] = []

    async def save(batch: finder.VerifiedBatch) -> None:
        saved.append(batch)

    verified = finder.FinderSession(
        pages, BASE, FIRST, "e2e-person", interactive=scope, turn_budget=1, on_verified=save
    )
    assert not asyncio.run(verified.report_verified([]))[1]
    assert asyncio.run(verified.snapshot())[0].startswith("Stopped: turn_limit")
    assert verified.turns == 1

    # Without a budget (a company careers page, one run a source) nothing is counted against.
    career = finder.FinderSession(pages, BASE, FIRST, "e2e-person")
    for _ in range(finder.MAX_TURNS + 5):
        assert not asyncio.run(career.snapshot())[1]
    assert (career.turns, career.stopped) == (finder.MAX_TURNS + 5, "")


def test_the_website_finder_time_is_shared_by_its_searches():
    """SPEC-browser's 840 s finder limit holds for the website, not for each of its searches:
    two searches of 500 s leave no time for a third (the sweep's own limit is far)."""
    site = SearchSite()
    now = [0.0]
    deadlines: list[float] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        deadlines.append(session.pages.deadline)
        done = await searching(site)(session)
        now[0] += 500  # the model's time in this search
        return done

    entry, _, leases = run(
        selected(title_include={"terms": ["robot", "drone", "rover"]}),
        site,
        ScriptedFinder(script),
        now=now,
    )
    assert finder.WALL_TIME_S == 840
    assert deadlines == [finder.WALL_TIME_S] * 2  # the website's 840 s, not the sweep's
    assert (entry["searches"], entry["error"]) == (2, "time_budget")
    assert len(leases.acquired) == 2  # the third search never asked for the browser


@pytest.mark.parametrize(
    ("contracts", "places", "counts"),
    [
        # (found, other_contract, unwanted) once both searches read the address.
        ([], ("Paris", "Lyon"), (0, 0, 1)),
        ([], ("Lyon", "Paris"), (0, 0, 1)),
        (["CDI", "Stage"], ("Paris CDD", "Lyon"), (0, 0, 1)),
        (["CDI", "Stage"], ("Lyon", "Paris CDD"), (0, 0, 1)),
        (["CDI", "Stage"], ("Paris", "Paris CDD"), (0, 1, 0)),
        (["CDI", "Stage"], ("Paris CDD", "Paris"), (0, 1, 0)),
        ([], ("Paris", "Paris"), (1, 0, 0)),
    ],
)
def test_an_address_read_two_ways_counts_once_and_the_refusal_wins(
    monkeypatch, contracts, places, counts
):
    """Two searches report one posting with other places: it counts in one place only, and an
    unwanted reading wins over another contract, which wins over a kept one (fail closed)."""
    monkeypatch.setattr(
        SearchSection, "location_exclude", property(lambda self: ["Lyon"]), raising=False
    )
    site = SearchSite(results={"robot": ["Fleet Lead"], "drone": ["Fleet Lead"]})
    place = dict(zip(("robot", "drone"), places, strict=True))

    async def script(session: finder.FinderSession) -> finder.Ran:
        query = session.interactive.query
        await session.interact("browser_type", {"ref": "e1", "text": query})
        await session.interact("browser_click", {"ref": "e3"})
        url = f"{site.base}jobs/fleet-lead"
        await session.navigate(url)
        session.report([{"title": "Fleet Lead", "location": place[query], "url": url, "text": "x"}])
        return finder.Ran()

    entry, _, _ = run(
        selected(title_include={"terms": ["robot", "drone"]}, contract_types=contracts),
        site,
        ScriptedFinder(script),
    )
    assert entry["searches"] == 2
    assert (entry["found"], entry.get("other_contract", 0), entry.get("unwanted", 0)) == counts


def test_an_error_in_one_search_stops_the_website_and_keeps_what_was_read():
    site = SearchSite(results={"robot": ["Robot Engineer"]})
    calls: list[str] = []

    async def script(session):
        calls.append(session.interactive.query)
        if session.interactive.query == "drone":
            # A page told the model to type other words: refused, as with one search.
            await session.interact("browser_type", {"ref": "e1", "text": "robot"})
            session.refused["injected"] += 1
            return finder.Ran()
        return await searching(site)(session)

    entry, _, leases = run(
        selected(title_include={"terms": ["robot", "drone", "rover"]}),
        site,
        ScriptedFinder(script),
    )
    assert calls == ["robot", "drone"]
    assert (entry["found"], entry["error"], entry["outcome"]) == (
        1,
        "unsupported_interaction",
        "partial",
    )
    assert entry["refused"] == {"injected": 1}
    assert leases.released == ["lease-1", "lease-2"]


def test_each_search_types_only_its_own_frozen_words_and_sends_its_own_scope():
    site = SearchSite()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        other = "drone" if session.interactive.query == "robot" else "robot"
        answers.append(await session.interact("browser_type", {"ref": "e1", "text": other}))
        return await searching(site)(session)

    entry, _, _ = run(
        selected(title_include={"terms": ["robot", "drone"]}, location={"terms": ["Paris"]}),
        site,
        ScriptedFinder(script),
    )
    assert answers == [("Refused: the query is outside this run's search scope.", True)] * 2
    first = {"start": BASE, "query": "robot", "filters": {"location": "Paris", "remote": "remote"}}
    second = {**first, "query": "drone"}
    assert site.scopes == [first] * 3 + [second] * 3  # navigate, type, click per search
    assert entry["searches"] == 2
    assert CANARY not in str(site.scopes)


def test_permission_is_read_again_before_each_later_search():
    site = SearchSite()
    policy = Policy()

    async def script(session):
        done = await searching(site)(session)
        policy.allowed = False  # turned off after the first search
        return done

    entry, _, leases = run(
        selected(title_include={"terms": ["robot", "drone"]}),
        site,
        ScriptedFinder(script),
        policy=policy,
    )
    assert entry["error"] == "permission_unavailable"
    assert entry["searches"] == 1
    assert len(leases.acquired) == 1  # refused before the second lease


class NoDatabasePolicy(platform_policy.PlatformPolicy):
    """The real policy's page counting and caps, with consent granted and nothing stored."""

    def __init__(self) -> None:
        super().__init__(sessions=None)  # type: ignore[arg-type]
        self.reserved: list[str] = []

    def _refusal(self, context, platform):
        return None

    def _reserve(self, context, platform):
        self.reserved.append(context.source_id)
        return None


def test_linkedin_pages_of_the_day_are_shared_by_all_its_searches():
    site = SearchSite(base=LINKEDIN, results={t: [f"{t} Engineer"] for t in ("a1", "b2", "c3")})
    policy = NoDatabasePolicy()
    entry, _, leases = run(
        selected(LINKEDIN, title_include={"terms": ["a1", "b2", "c3"]}),
        site,
        ScriptedFinder(searching(site)),
        policy=policy,
        sweep_id=str(uuid.uuid4()),
    )
    [pages] = [run.pages for run in policy._runs.values()]
    assert pages == {"linkedin": platform_policy.PAGES["linkedin"]} == {"linkedin": 5}
    assert entry["error"] == "page_budget"
    assert entry["searches"] == 3  # the third opened its page five, then the cap held
    assert entry["found"] == 2
    assert len(leases.acquired) == 3
    assert policy.reserved == [policy.reserved[0]] * 3  # one source, reserved by each search


def test_one_contract_is_the_website_filter_and_several_are_checked_after_reading():
    one = searches_for(selected(contract_types=["CDI"]))
    assert dict(one.scopes[0].filters)["contract"] == "CDI"
    assert one.contracts == frozenset()

    site = SearchSite(results={"robot": ["Robot Engineer", "Robot Intern", "Robot Alternance"]})
    several = selected(contract_types=["Stage", "Alternance"])
    assert all("contract" not in dict(s.filters) for s in searches_for(several).scopes)
    entry, _, _ = run(several, site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["other_contract"]) == (2, 1)


def test_unwanted_titles_and_places_are_context_and_dropped_after_reading(monkeypatch):
    site = SearchSite(results={"robot": ["Robot Engineer", "Robot Sales Lead"]})
    search = selected(title_exclude={"terms": ["sales"]})
    [scope] = searches_for(search).scopes
    assert '"title_exclude"' in scope.criteria
    entry, _, _ = run(search, site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["unwanted"]) == (1, 1)

    # The plain place lists of another task (field location_exclude), read defensively.
    monkeypatch.setattr(
        SearchSection,
        "location_exclude",
        property(lambda self: ["Paris"]),
        raising=False,
    )
    entry, _, _ = run(selected(), site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["unwanted"]) == (0, 2)


@pytest.mark.parametrize(
    ("term", "plain"),
    [
        ("robot", True),
        ("Robot", True),
        ("robot engineer", True),
        ("full-stack", True),
        ("full-stack engineer", True),
        ("chargé d'affaires", True),
        ("ingénieur", True),
        ("机器人", True),
        ("c3po", True),
        ("ai engineer 2", True),
        ("robot.*", False),
        ("robot.", False),
        (".", False),
        ("robot|drone", False),
        ("(robot)", False),
        ("robots?", False),
        ("robot+", False),
        ("^robot", False),
        ("robot$", False),
        ("[rR]obot", False),
        ("robot{2}", False),
        (r"robot\b", False),
        (r"\w+", False),
        ("c++", False),
        ("c#", False),
        ("robot  engineer", False),
        (" robot", False),
        ("robot ", False),
        ("-robot", False),
        ("robot-", False),
        ("robot--arm", False),
        ("robot_arm", False),
        ("robot/arm", False),
        ("node.js", False),
        ("'", False),
        ("", False),
    ],
)
def test_a_regular_expression_is_typed_only_when_it_is_a_plain_word(term, plain):
    assert bool(PLAIN.fullmatch(term)) is plain


def test_a_plain_regular_expression_is_searched_and_any_other_stays_unsupported():
    plain = selected(title_include={"terms": ["robot", "drone"], "advanced_regex": True})
    assert [s.query for s in searches_for(plain).scopes] == ["robot", "drone"]
    places = selected(location={"terms": ["Paris|Lyon"], "advanced_regex": True})
    with pytest.raises(ValueError, match="unsupported_criteria"):
        searches_for(places)
    entry, _, leases = run(places, SearchSite(), ScriptedFinder(lambda _: None))
    assert entry["reason"] == "unsupported_criteria"
    assert not leases.acquired


def test_no_place_means_no_location_filter():
    [scope] = searches_for(selected()).scopes
    assert dict(scope.filters) == {"remote": "remote"}
    assert {"title_exclude", "location", "location_exclude"} <= interactive.CRITERIA


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ({"searches": 6, "searches_planned": 6, "searches_total": 9}, (6, 6, 9)),
        ({}, (0, 0, 0)),  # an older row, or a source of another kind
        ({"searches": -1, "searches_planned": True, "searches_total": "9"}, (0, 0, 0)),
    ],
)
def test_the_summary_reports_the_searches_that_ran_and_the_cap(stored, shown):
    from app.jobs import sweep_router
    from tests.opportunities.test_o8_summary import row

    entry = {"kind": "indeed", "query": "", "mode": "llm_browser", "found": 1, **stored}
    [source] = sweep_router.sweep_out(row([entry])).sources
    assert (source.searches, source.searches_planned, source.searches_total) == shown
