"""One agent per job website, searching with all the person's saved criteria (jobs J18, owner
2026-10-27): one lease and one finder run a website, the saved titles and places in one frozen
scope, no cap on the agent's turns, pages, tokens or verified postings, and the sweep's time
shared fairly among the websites still to run.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import pytest

from app.browser import finder, platform_policy
from app.browser.sources import interactive
from app.browser.sources.interactive import PLAIN, searches_for
from app.profile.schemas import SearchSection
from tests.browser.test_b9_site import ScriptedFinder
from tests.browser.test_b13_runtime import BASE, FIRST
from tests.browser.test_selected_interactive import CANARY, execute, selected

LINKEDIN = "https://www.linkedin.com/jobs/"


@dataclass
class SearchSite:
    """A native search website: Search lists the postings of the words typed (and the place
    chosen), on a result page whose address carries them; each posting has its page."""

    base: str = BASE
    results: dict[str, list[str]] = field(default_factory=dict)  # typed words: posting titles
    scopes: list[dict[str, Any]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    current: str = ""
    typed: str = ""
    place: str = ""
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
        elif name == "browser_select_option":
            self.place = args["values"][0]
        elif name == "browser_click":
            titles = self.results.get(self.typed, [])
            query = {"q": self.typed, **({"location": self.place} if self.place else {})}
            self.current = f"{self.base}search?{urlencode(query)}"
            self.pages[self.current] = (
                "\n".join(
                    f'- link "{title}" [ref=e{9 + n}]\n  - /url: {self.base}jobs/{self.slug(title)}'
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
    """The model: for each saved title, and each saved place when there are some, types the
    title, chooses the place, presses Search, opens and reports every result, as a person
    would; until a tool tells it to stop."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        scope = session.interactive
        if log is not None:
            log.append(session)
        ran = finder.Ran(input_tokens=tokens, output_tokens=10)
        for title in scope.titles:
            for place in scope.places or (None,):
                if (await session.interact("browser_type", {"ref": "e1", "text": title}))[1]:
                    return ran
                if place is not None:
                    chosen = {"ref": "e2", "values": [place]}
                    if (await session.interact("browser_select_option", chosen))[1]:
                        return ran
                if (await session.interact("browser_click", {"ref": "e3"}))[1]:
                    return ran
                for posting in site.results.get(title, []):
                    url = f"{site.base}jobs/{site.slug(posting)}"
                    _, stop = await session.navigate(url)
                    if stop:
                        return ran
                    session.report(
                        [{"title": posting, "location": place or "Paris", "url": url, "text": "x"}]
                    )
        return ran

    return script


class NoPause:
    """A pacer with no pause: many pages fit in the lease's own 900 s (a real lease's maximum
    stays a bound, test_selected_interactive)."""

    async def reserve(self, host: str, pause: float) -> float:
        return 0.0


def run(search: SearchSection, site: SearchSite, agent: Any, **kw: Any):
    entries, stored, leases, _, _ = asyncio.run(
        execute(search, "e2e-person", agent, tools=site, deadline=100_000, **kw)
    )
    [entry] = entries
    return entry, stored, leases


# --- one agent, one lease, every saved title and place ----------------------------------------


def test_one_agent_runs_every_saved_search_in_one_session_on_one_lease():
    site = SearchSite(results={"robot": ["Robot Engineer"], "drone": ["Drone Pilot"]})
    seen: list[finder.FinderSession] = []
    search = selected(
        title_include={"terms": ["robot", "drone"]}, location={"terms": ["Paris", "Lyon"]}
    )
    entry, stored, leases = run(search, site, ScriptedFinder(searching(site, log=seen)))
    [session] = seen  # one run for the website, whatever the number of titles and places
    assert (session.interactive.titles, session.interactive.places) == (
        ("robot", "drone"),
        ("Paris", "Lyon"),
    )
    assert len(leases.acquired) == 1
    assert leases.released == ["lease-1"]
    assert entry["searches"] == 4
    assert entry["searched"] == [
        {"title": "robot", "place": "Paris"},
        {"title": "robot", "place": "Lyon"},
        {"title": "drone", "place": "Paris"},
        {"title": "drone", "place": "Lyon"},
    ]
    # The one frozen scope goes with every call: every saved value, never the instructions.
    frozen = {
        "start": BASE,
        "titles": ["robot", "drone"],
        "places": ["Paris", "Lyon"],
        "filters": {"remote": "remote"},
    }
    assert site.scopes == [frozen] * len(site.actions)
    assert CANARY not in json.dumps(site.scopes)
    # Each posting once per address, though two searches (two places) listed it.
    assert (entry["found"], entry["error"], entry["outcome"]) == (
        2,
        "finding_persistence_pending",
        "partial",
    )
    assert entry["refused"] == {"duplicate": 2}
    assert entry["tokens"] == {"input": 1000, "output": 10}
    assert stored == []


def test_the_agent_gets_all_the_person_s_criteria_as_context_never_to_type():
    search = selected(
        title_exclude={"terms": ["sales"]},
        location_exclude={"terms": ["Lyon"], "advanced_regex": False},
        contract_types=["CDI", "Stage"],
        seniority=["senior"],
        industries={"terms": ["robotics"]},
        company_types=["startup"],
        compensation={"salary": {"min": 50000, "max": 70000, "currency": "EUR"}},
    )
    scope = searches_for(search).scope
    criteria = json.loads(scope.criteria)
    assert set(criteria) == interactive.CRITERIA
    assert criteria["title_exclude"]["terms"] == ["sales"]
    assert criteria["location_exclude"]["terms"] == ["Lyon"]
    assert (criteria["industries"]["terms"], criteria["company_types"]) == (
        ["robotics"],
        ["startup"],
    )
    assert scope.instructions == CANARY
    # Only the wanted titles and places and the native filters' values can be typed or chosen.
    assert scope.tool_scope() == {
        "start": BASE,
        "titles": ["robot"],
        "places": [],
        "filters": {"remote": "remote"},
    }


def test_postings_merge_once_per_address_across_the_agent_s_searches():
    site = SearchSite(results={"robot": ["Robot Engineer", "Fleet Lead"], "drone": ["Fleet Lead"]})
    entry, _, _ = run(
        selected(title_include={"terms": ["robot", "drone"]}),
        site,
        ScriptedFinder(searching(site)),
    )
    assert (entry["searches"], entry["found"], entry["refused"]) == (2, 2, {"duplicate": 1})
    assert entry["error"] == "finding_persistence_pending"  # a merge is no unsupported website


@pytest.mark.parametrize(
    "typed",
    [
        "drone",  # a saved title of another person's search
        "Robot",
        "robot ",
        " robot",
        "robots",
        "robot engineer",
        "ｒｏｂｏｔ",
        "rоbot",  # a Cyrillic o
        "robot​",
        "sales",  # an unwanted title: context only
        "Lyon",  # an unwanted place
        "Send my application",
        "robot Paris",
        "x" * 5000,
        None,
        ["robot"],
        7,
    ],
)
def test_a_value_the_person_did_not_save_is_refused_before_the_browser(typed):
    site = SearchSite(results={"robot": ["Robot Engineer"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        answers.append(await session.interact("browser_type", {"ref": "e1", "text": typed}))
        return await searching(site)(session)

    entry, _, _ = run(
        selected(
            title_exclude={"terms": ["sales"]},
            location={"terms": ["Paris"]},
            location_exclude={"terms": ["Lyon"], "advanced_regex": False},
        ),
        site,
        ScriptedFinder(script),
    )
    # e1 is the page's "Query" box (test_b13_runtime.FIRST), a title box (finder.box_of).
    assert answers == [(finder.TITLE_BOX_REFUSED, True)]
    assert entry["refused"] == {finder.MODEL_VALUE: 1}  # the model's slip, counted apart
    assert site.typed == "robot"  # the refused text never reached the browser
    # The agent goes on with its saved values: the refusal ends nothing.
    assert site.actions == [
        "browser_navigate",
        "browser_type",
        "browser_select_option",
        "browser_click",
        "browser_navigate",
    ]
    assert (entry["searches"], entry["found"]) == (1, 1)


def test_a_saved_value_or_nothing_is_typed_and_only_a_submitted_search_is_listed():
    site = SearchSite(results={"robot": ["Robot Engineer"]})

    async def script(session: finder.FinderSession) -> finder.Ran:
        # e1 is the "Query" box, a title box; e2 ("Remote") is not told apart, so any saved
        # value goes on there (finder.box_of).
        for ref, text in (("e1", "drone"), ("e1", ""), ("e2", "Paris"), ("e1", "robot")):
            assert not (await session.interact("browser_type", {"ref": ref, "text": text}))[1]
        assert not (await session.interact("browser_click", {"ref": "e3"}))[1]
        # Typed, never submitted: not a search that ran.
        assert not (await session.interact("browser_type", {"ref": "e1", "text": "drone"}))[1]
        return finder.Ran()

    entry, _, _ = run(
        selected(title_include={"terms": ["robot", "drone"]}, location={"terms": ["Paris"]}),
        site,
        ScriptedFinder(script),
    )
    assert entry["searched"] == [{"title": "robot", "place": "Paris"}]
    assert entry["searches"] == 1


# --- no budget of its own ------------------------------------------------------------------------


def test_an_agent_run_of_more_than_40_turns_and_20_pages_and_100_postings_is_allowed():
    """Owner, 2026-10-27, its budget per website: "no limit". 150 postings, each opened and
    reported verified, are 300 tool calls and 152 pages, all kept."""
    titles = [f"Robot Engineer {n}" for n in range(150)]
    site = SearchSite(results={"robot": titles})
    saved: list[str] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        calls = 0

        async def save(batch: finder.VerifiedBatch) -> None:
            saved.extend(f.title for f in batch.findings)

        session.on_verified = save
        for step in (
            session.interact("browser_type", {"ref": "e1", "text": "robot"}),
            session.interact("browser_click", {"ref": "e3"}),
        ):
            assert not (await step)[1]
            calls += 1
        for title in titles:
            url = f"{site.base}jobs/{site.slug(title)}"
            assert not (await session.navigate(url))[1], calls
            answer, error = await session.report_verified(
                [{"title": title, "url": url, "text": "x"}]
            )
            assert not error, answer
            assert json.loads(answer)["kept"] == 1, answer
            calls += 2
        assert calls == 302 > finder.MAX_TURNS
        return finder.Ran(input_tokens=2 * finder.TOKEN_BUDGET, output_tokens=10)

    entry, _, leases = run(selected(), site, ScriptedFinder(script), pacer=NoPause())
    assert len(saved) == 150 > finder.POSTINGS_MAX
    # The start page, the page typed on, the results and the 150 postings.
    assert entry["result_pages"] == 153 > finder.PAGES_MAX
    assert entry["error"] is None
    assert entry["tokens"]["input"] == 2 * finder.TOKEN_BUDGET
    assert len(leases.acquired) == 1


def test_the_agent_is_told_the_seconds_it_has_left():
    site = SearchSite(results={"robot": ["Robot Engineer"]})
    views: list[int] = []
    now = [0.0]

    async def script(session: finder.FinderSession) -> finder.Ran:
        for text in ("robot",):
            answer, _ = await session.interact("browser_type", {"ref": "e1", "text": text})
            views.append(json.loads(answer)["seconds_left"])
        now[0] += 120
        answer, _ = await session.interact("browser_click", {"ref": "e3"})
        views.append(json.loads(answer)["seconds_left"])
        return finder.Ran()

    asyncio.run(
        execute(selected(), "e2e-person", ScriptedFinder(script), tools=site, deadline=900, now=now)
    )
    # Its share of the sweep's 900 s (it is the only website), less the pacing and the 120 s.
    assert views[0] - views[1] in range(120, 140)
    assert 0 < views[1] < views[0] <= 900


# --- the sweep's time, shared fairly ----------------------------------------------------------


def two_websites(**search: Any) -> SearchSection:
    found = selected(**search)
    found.interactive_sites.append(
        found.interactive_sites[0].model_copy(update={"url": BASE + "second/"})
    )
    return found


def websites(count: int, **search: Any) -> SearchSection:
    found = selected(**search)
    for n in range(1, count):
        found.interactive_sites.append(
            found.interactive_sites[0].model_copy(update={"url": f"{BASE}site{n}/"})
        )
    return found


class Websites(SearchSite):
    """Several websites of the fake: each start page is a search page."""

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_navigate" and args["url"].startswith(BASE + "site"):
            self.pages[args["url"]] = FIRST
        return await super().call(name, args)


@pytest.mark.parametrize(
    ("count", "deadlines"),
    [
        (2, [500.0, 1000.0]),
        (3, [1000 / 3, 550.0, 1000.0]),
    ],
)
def test_the_sweep_time_is_shared_fairly_between_the_websites(count, deadlines):
    """A website gets the sweep's time left divided by the websites left: each here takes
    100 s, and what it leaves goes to the next ones."""
    now = [0.0]
    seen: list[float] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        seen.append(session.pages.deadline)
        await session.interact("browser_type", {"ref": "e1", "text": "robot"})
        await session.interact("browser_click", {"ref": "e3"})
        now[0] = 100.0 * len(seen)
        return finder.Ran()

    entries, _, leases, _, _ = asyncio.run(
        execute(
            websites(count),
            "e2e-person",
            ScriptedFinder(script),
            tools=Websites(),
            deadline=1000,
            now=now,
        )
    )
    assert seen == pytest.approx(deadlines)
    assert [entry["error"] for entry in entries] == [None] * count
    assert len(leases.acquired) == count


def test_a_website_past_its_share_stops_there_and_the_next_one_gets_its_own():
    now = [0.0]
    seen: list[float] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        seen.append(session.pages.deadline)
        await session.interact("browser_type", {"ref": "e1", "text": "robot"})
        await session.interact("browser_click", {"ref": "e3"})
        if len(seen) == 1:
            now[0] = 450.0  # a page now would end past its 500 s
            answer, stop = await session.interact("browser_type", {"ref": "e1", "text": "robot"})
            assert (answer, stop) == (
                "Stopped: time_budget. Report what you found, then stop.",
                True,
            )
        return finder.Ran()

    entries, _, leases, _, _ = asyncio.run(
        execute(
            websites(2),
            "e2e-person",
            ScriptedFinder(script),
            tools=Websites(),
            deadline=1000,
            now=now,
        )
    )
    assert seen == [500.0, 1000.0]  # the second: 450 s in, all the 550 s left
    assert [entry["error"] for entry in entries] == ["time_budget", None]
    assert len(leases.acquired) == 2


# --- the platform's own caps --------------------------------------------------------------------


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


def test_linkedin_pages_of_the_sweep_still_hold_for_its_agent():
    """LinkedIn's account safety cap stays (5 pages a sweep, one sweep a day, test_b14_policy):
    the agent's sixth page is refused there, whatever its own lack of a cap."""
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
    # The second search's posting was the fifth page: the third search's typing is refused.
    assert (entry["searches"], entry["found"]) == (2, 2)
    assert len(leases.acquired) == 1
    assert len(policy.reserved) == 1


def test_another_platform_has_no_page_cap_of_the_policy():
    site = SearchSite(
        base="https://www.indeed.com/jobs/",
        results={"robot": [f"Robot Engineer {n}" for n in range(30)]},
    )
    entry, _, _ = run(
        selected("https://www.indeed.com/jobs/"),
        site,
        ScriptedFinder(searching(site)),
        policy=NoDatabasePolicy(),
        sweep_id=str(uuid.uuid4()),
    )
    assert platform_policy.PAGES == {"linkedin": 5}
    assert (entry["error"], entry["found"]) == ("finding_persistence_pending", 30)
    assert entry["result_pages"] == 33 > finder.PAGES_MAX  # the start, typed, results, 30 postings


# --- per person --------------------------------------------------------------------------------


def test_each_person_s_agent_gets_only_that_person_s_values_and_lease():
    scopes: dict[str, Any] = {}

    async def script(session: finder.FinderSession) -> finder.Ran:
        scopes[session.user_id] = session.interactive
        return finder.Ran()

    leases_of = {}
    for person, title in (("e2e-alice", "robot"), ("e2e-bob", "drone")):
        _, _, leases, _, _ = asyncio.run(
            execute(
                selected(title_include={"terms": [title]}, interactive_instructions=person),
                person,
                ScriptedFinder(script),
                tools=SearchSite(),
            )
        )
        leases_of[person] = leases.acquired
    assert (scopes["e2e-alice"].titles, scopes["e2e-bob"].titles) == (("robot",), ("drone",))
    assert (scopes["e2e-alice"].instructions, scopes["e2e-bob"].instructions) == (
        "e2e-alice",
        "e2e-bob",
    )
    assert [user for user, _, _ in leases_of["e2e-alice"]] == ["e2e-alice"]
    assert [user for user, _, _ in leases_of["e2e-bob"]] == ["e2e-bob"]


# --- what is typed, what is filtered after reading ---------------------------------------------


@pytest.mark.parametrize(
    ("first", "then", "saved", "unwanted"),
    [
        ("Robot Engineer", "Robot Sales Lead", ["Robot Engineer"], 0),  # stored stays, once
        ("Robot Sales Lead", "Robot Engineer", [], 1),  # unwanted first: never stored
        ("Robot Engineer", "Robot Engineer", ["Robot Engineer"], 0),
    ],
)
def test_a_stored_address_stays_stored_once_whatever_a_later_reading_says(
    first, then, saved, unwanted
):
    """The verified batches on their way to the store (jobs J11): a posting is saved once, and
    an earlier unwanted reading keeps a later wanted one out."""
    stored: list[str] = []

    class Commits:
        async def save(self, source_id, host, batch):
            stored.extend(finding.title for finding in batch.findings)

    planned = searches_for(selected(title_exclude={"terms": ["sales"]}))
    site = interactive._Site(planned, commits=Commits(), source_id="s", host="jobs.example.test")
    url = BASE + "jobs/fleet"

    def batch(title: str) -> finder.VerifiedBatch:
        return finder.VerifiedBatch(
            (finder.VerifiedFinding(title, "Paris", url, title, None, None, {}, ""),)
        )

    asyncio.run(site.verified(batch(first)))
    asyncio.run(site.verified(batch(then)))
    assert (stored, len(site.unwanted), site.found) == (saved, unwanted, set())


def test_one_contract_is_the_website_filter_and_several_are_checked_after_reading():
    one = searches_for(selected(contract_types=["CDI"]))
    assert dict(one.scope.filters)["contract"] == "CDI"
    assert one.contracts == frozenset()

    site = SearchSite(results={"robot": ["Robot Engineer", "Robot Intern", "Robot Alternance"]})
    several = selected(contract_types=["Stage", "Alternance"])
    assert "contract" not in dict(searches_for(several).scope.filters)
    entry, _, _ = run(several, site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["other_contract"]) == (2, 1)


def test_unwanted_titles_and_places_are_context_and_dropped_after_reading():
    site = SearchSite(results={"robot": ["Robot Engineer", "Robot Sales Lead"]})
    search = selected(title_exclude={"terms": ["sales"]})
    assert '"title_exclude"' in searches_for(search).scope.criteria
    entry, _, _ = run(search, site, ScriptedFinder(searching(site)))
    assert (entry["found"], entry["unwanted"]) == (1, 1)

    places = selected(
        location={"terms": ["Paris"]},
        location_exclude={"terms": ["Paris"], "advanced_regex": False},
    )
    entry, _, _ = run(places, site, ScriptedFinder(searching(site)))
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
    assert searches_for(plain).scope.titles == ("robot", "drone")
    places = selected(location={"terms": ["Paris|Lyon"], "advanced_regex": True})
    with pytest.raises(ValueError, match="unsupported_criteria"):
        searches_for(places)
    entry, _, leases = run(places, SearchSite(), ScriptedFinder(lambda _: None))
    assert entry["reason"] == "unsupported_criteria"
    assert not leases.acquired


@pytest.mark.parametrize(
    "terms",
    [
        [
            f"robot{n}" for n in range(finder.SCOPE_VALUES_MAX + 1)
        ],  # more values than browserd takes
        ["r" * (finder.SCOPE_VALUE_MAX + 1)],  # a value longer than browserd takes
        ["é" * 190 + f"{n:03d}" for n in range(30)],  # over SCOPE_BYTES_MAX as sent
    ],
)
def test_a_scope_browserd_would_refuse_is_unsupported_before_a_lease(terms):
    search = selected(title_include={"terms": terms, "advanced_regex": True})
    with pytest.raises(ValueError, match="invalid discovery scope"):
        searches_for(search)
    entry, _, leases = run(search, SearchSite(), ScriptedFinder(lambda _: None))
    assert entry["reason"] == "unsupported_criteria"
    assert not leases.acquired


def test_no_place_means_no_place_to_type_and_a_search_by_title():
    scope = searches_for(selected()).scope
    assert (scope.places, dict(scope.filters)) == ((), {"remote": "remote"})


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (
            {"searches": 2, "searched": [{"title": "robot", "place": "Paris"}, {"title": "drone"}]},
            (2, [("robot", "Paris"), ("drone", None)]),
        ),
        ({}, (0, [])),  # an older row, or a source of another kind
        (
            {"searches": -1, "searched": [{"title": 3}, "robot", {"place": "Paris"}, None]},
            (0, []),
        ),
        ({"searches": True, "searched": {"title": "robot"}}, (0, [])),
        ({"searches": 60, "searched": [{"title": f"t{n}"} for n in range(60)]}, (60, 50)),
    ],
)
def test_the_summary_names_the_searches_that_ran_and_counts_them(stored, shown):
    from app.jobs import sweep_router
    from tests.opportunities.test_o8_summary import row

    entry = {"kind": "indeed", "query": "", "mode": "llm_browser", "found": 1, **stored}
    [source] = sweep_router.sweep_out(row([entry])).sources
    listed = [(one.title, one.place) for one in source.searched]
    assert source.searches == shown[0]
    assert (len(listed) if isinstance(shown[1], int) else listed) == shown[1]


def test_the_summary_lists_at_most_fifty_searches_and_counts_all():
    site = SearchSite()
    titles = [f"robot{n}" for n in range(60)]
    entry, _, _ = run(
        selected(title_include={"terms": titles}),
        site,
        ScriptedFinder(searching(site)),
        pacer=NoPause(),
    )
    assert entry["searches"] == 60
    assert [one["title"] for one in entry["searched"]] == titles[:50]
