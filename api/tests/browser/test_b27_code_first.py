"""B27 (owner, 2026-11-17: "let the agent write code in a sandbox or whatever"): the website
search agent reads every result card with one script, its result kept and searchable as a page
is; the app opens and verifies a posting the agent saw and did not open; the agent cannot give
up before it tried the search box, a search address and the listing pages, or met a block code
saw; browserd's script limits rise for a discovery's lease only; a page is parsed off the event
loop (#797).

The harness is test_b20_agent's (browserd's answers from `Site`, the registered tools called the
way the SDK calls them). Invented hosts only, never a real job website.
"""

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from tests.browser.test_b20_agent import HOME, RESULTS, START, Site, run, selected, text_of, tool

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd.tools import actions  # noqa: E402

# A board whose list renders only from script data: its snapshot holds no posting link.
BOARD = START + "search?q=robot&page=1"
BOARD_PAGE = '- heading "Robot jobs" [level=1]\n- paragraph: Loading results'
SEEN_1 = START + "jobs/7001"
SEEN_2 = START + "jobs/7002"  # on page 2, which the script fetched from the board's origin
MADE_UP = START + "jobs/9999"
POSTING_1 = '- heading "Robot Arm Engineer" [level=1]\n- paragraph: Build invented robot arms.'
POSTING_2 = '- heading "Robot Welder" [level=1]\n- paragraph: Weld invented robot frames.'
CHECK_PAGE = '- heading "Just a moment" [level=1]\n- paragraph: Checking your browser'
CARDS = [
    {"title": "Robot Arm Engineer", "company": "Invented Robotics", "link": SEEN_1},
    {"title": "Robot Welder", "company": "Invented Robotics", "link": "/jobs/7002"},
]
LISTING = START + "fr/pages/emploi-robotique"
PLANTED = "b27-planted-5d1e"  # page content in a script's result


class TabSite(Site):
    """`Site` with browserd's tabs (new, close, select) and the page of each tab."""

    def __init__(self, pages: dict[str, str], **kw: Any) -> None:
        super().__init__(pages, **kw)
        self.tabs: list[str] = [""]
        self.at = 0

    def listed(self) -> list[dict[str, Any]]:
        return [
            {"index": i, "url": url, "title": "", "current": i == self.at}
            for i, url in enumerate(self.tabs)
        ]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "browser_tabs":
            answer = await super().call(name, args)
            self.tabs[self.at] = self.current
            return answer
        args.pop("_discovery"), args.pop("_discovery_scope")
        self.calls.append((name, dict(args)))
        if args["action"] == "new":
            self.tabs.append("about:blank")
            self.at = len(self.tabs) - 1
        elif args["action"] == "close":
            del self.tabs[args["index"]]
            self.at = 0
        elif args["action"] == "select":
            self.at = args["index"]
        self.current = self.tabs[self.at]
        return {"ok": True, "tabs": self.listed()}


def board(**kw: Any) -> TabSite:
    pages = {START: HOME, BOARD: BOARD_PAGE, SEEN_1: POSTING_1, SEEN_2: POSTING_2, LISTING: ""}
    return TabSite(pages, **kw)


def scripted(value: Any) -> dict[str, Any]:
    return {"ok": True, "result": json.dumps(value), "cut": False}


def report_of(answer: dict[str, Any]) -> dict[str, Any]:
    return json.loads(text_of(answer))


async def stored(session: finder.FinderSession) -> list[finder.VerifiedBatch]:
    batches: list[finder.VerifiedBatch] = []

    async def keep(batch: finder.VerifiedBatch) -> None:
        batches.append(batch)

    session.on_verified = keep
    return batches


def test_a_list_rendered_from_script_data_is_read_and_its_postings_opened_by_the_app():
    """Acceptance 3 and 6: the board's list renders only from script data and its second page
    came by a same origin fetch, so the agent never opened either posting. Reported from the
    script's result, each is opened by the app in a tab of its own, after a paced pause, its
    title checked and stored; the agent's tab is selected again; a made up address is refused."""
    site = board(evaluated=scripted(CARDS))
    seen: dict[str, Any] = {}
    pauses: list[float] = []

    async def script(session):
        batches = await stored(session)
        sleep = session.pages.sleep

        async def paced(seconds: float) -> None:
            pauses.append(seconds)
            await sleep(seconds)

        session.pages.sleep = paced
        await tool(session, "browser_navigate", url=BOARD)
        seen["script"] = report_of(await tool(session, "browser_evaluate", function="() => 1"))
        postings = [
            {"title": "Robot Arm Engineer", "url": SEEN_1, "text": "A card."},
            {"title": "Robot Welder", "url": "/jobs/7002", "text": "A card."},
            {"title": "Robot Painter", "url": MADE_UP, "text": "A card."},
        ]
        seen["report"] = report_of(await tool(session, "report_postings", postings=postings))
        seen["batches"] = batches
        seen["kept_tab"] = session.kept[0].url
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert "Robot Welder" in seen["script"]["untrusted_script_result"]
    assert seen["report"]["kept"] == 2
    assert seen["report"]["refused"] == [
        {"index": 2, "reason": "not_seen", "do": finder.REPORT_ADVICE["not_seen"]}
    ]
    stored_urls = [f.url for batch in seen["batches"] for f in batch.findings]
    assert stored_urls == [SEEN_1, SEEN_2]
    assert [f.title for batch in seen["batches"] for f in batch.findings] == [
        "Robot Arm Engineer",
        "Robot Welder",
    ]
    # One at a time: a tab of its own, the address, closed, the agent's tab again.
    tabs = [args for name, args in site.calls if name == "browser_tabs"]
    assert (
        tabs
        == [
            {"action": "new"},
            {"action": "close", "index": 1},
            {"action": "select", "index": 0},
        ]
        * 2
    )
    assert ("browser_navigate", {"url": MADE_UP}) not in site.calls
    assert len(pauses) == 2
    assert all(2.0 <= p <= 4.0 for p in pauses)
    assert seen["kept_tab"] == BOARD  # the agent's snapshot stays its own
    assert entry["opened_by_app"] == 2


def test_a_page_the_app_opens_without_the_title_is_refused_and_never_acted_on():
    """A security check on the reported address: the app only navigates and reads, never
    clicks or types, and the report is refused with what to do."""
    site = board(evaluated=scripted(CARDS))
    site.pages[SEEN_1] = CHECK_PAGE
    seen: dict[str, Any] = {}

    async def script(session):
        await stored(session)
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_evaluate", function="() => 1")
        posting = {"title": "Robot Arm Engineer", "url": SEEN_1, "text": "A card."}
        seen["report"] = report_of(await tool(session, "report_postings", postings=[posting]))
        return finder.Ran()

    run(selected(), site, script)
    [refusal] = seen["report"]["refused"]
    assert refusal["reason"] == "not_on_opened_page"
    acted = {name for name, _ in site.calls}
    assert not acted & {"browser_click", "browser_type", "browser_press_key"}


def test_no_free_tab_leaves_the_posting_to_the_agent():
    """Three tabs open: the app cannot open its own, so the report is refused as before."""
    site = board(evaluated=scripted(CARDS))
    site.tabs = ["", "a", "b"]
    seen: dict[str, Any] = {}

    async def script(session):
        await stored(session)
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_evaluate", function="() => 1")

        async def full(name, args):
            return {"ok": False, "error": "too_many_tabs"}

        call = site.call

        async def refusing(name, args):
            if name == "browser_tabs":
                args.pop("_discovery"), args.pop("_discovery_scope")
                return await full(name, args)
            return await call(name, args)

        site.call = refusing  # type: ignore[method-assign]
        posting = {"title": "Robot Arm Engineer", "url": SEEN_1, "text": "A card."}
        seen["report"] = report_of(await tool(session, "report_postings", postings=[posting]))
        return finder.Ran()

    run(selected(), site, script)
    assert seen["report"]["refused"][0]["reason"] == "detail_not_opened"


def test_a_long_script_result_is_kept_and_read_with_find_and_snapshot():
    """Acceptance 1: a result of 300,000 characters answers its first 6,000 under an untrusted
    key; browser_find and browser_snapshot read the rest with source "script"."""
    cards = [{"title": f"Robot Engineer {n}", "link": f"/jobs/{n}"} for n in range(5_000)]
    cards.append({"title": f"Robot Tail {PLANTED}", "link": "/jobs/tail"})
    site = board(evaluated=scripted(cards))
    seen: dict[str, Any] = {}

    async def script(session):
        seen["eval"] = report_of(await tool(session, "browser_evaluate", function="() => 1"))
        seen["find"] = report_of(
            await tool(session, "browser_find", pattern=PLANTED, source="script")
        )
        seen["window"] = report_of(
            await tool(session, "browser_snapshot", offset=200_000, source="script")
        )
        seen["page"] = report_of(await tool(session, "browser_find", pattern=PLANTED))
        seen["links"] = dict(session.script_links)
        return finder.Ran()

    run(selected(), site, script)
    shown = seen["eval"]["untrusted_script_result"]
    assert len(shown) <= finder.COMPACT_MAX
    assert PLANTED not in shown
    trusted = {k: v for k, v in seen["eval"].items() if not k.startswith("untrusted_")}
    assert PLANTED not in json.dumps(trusted)
    assert 'source="script"' in seen["eval"]["script"]
    [match] = seen["find"]["untrusted_page_matches"]
    assert PLANTED in match["lines"]
    assert seen["find"]["source"] == "script"
    assert seen["window"]["offset"] == 200_000
    assert seen["window"]["untrusted_script_result"]
    assert "untrusted_page_snapshot" not in seen["window"]
    assert seen["page"]["matching_lines"] == 0  # the page is another source
    assert START + "jobs/tail" in seen["links"]


def test_find_and_snapshot_of_a_script_before_any_script_say_so():
    site = board()
    seen: dict[str, Any] = {}

    async def script(session):
        seen["find"] = await tool(session, "browser_find", pattern="x", source="script")
        seen["snap"] = await tool(session, "browser_snapshot", source="script")
        return finder.Ran()

    run(selected(), site, script)
    assert text_of(seen["find"]) == finder.NO_SCRIPT
    assert text_of(seen["snap"]) == finder.NO_SCRIPT


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        (json.dumps("a\nb"), "a\nb"),
        (json.dumps([{"t": "x"}]), '[\n {\n  "t": "x"\n }\n]'),
        (json.dumps(42), "42"),
        ("not json", "not json"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_script_result_is_kept_one_value_per_line(raw, kept):
    assert finder._script_text(raw) == kept


# What a script result names that a report may give (B27): absolute http(s) addresses and
# quoted root paths; nothing else.
LINK_TABLE = [
    ('{"link": "https://jobs.example.test/jobs/1"}', [START + "jobs/1"]),
    ('"/jobs/2"', [START + "jobs/2"]),
    ('"//cdn.example.test/jobs/3"', ["https://cdn.example.test/jobs/3"]),
    ("see http://jobs.example.test/jobs/4 now", ["http://jobs.example.test/jobs/4"]),
    ('"jobs/5"', []),  # relative to a page: not read
    ('"javascript:alert(1)"', []),
    ('"file:///etc/passwd"', []),
    ('"ftp://jobs.example.test/x"', []),
    ("/jobs/6 unquoted", []),
    ('"/jobs/7" and "/jobs/7"', [START + "jobs/7"]),
    ("", []),
]


@pytest.mark.parametrize(("text", "links"), LINK_TABLE)
def test_the_addresses_a_script_result_names(text, links):
    assert finder._script_links(START + "search", text) == links


def test_the_addresses_a_script_result_names_are_bounded(monkeypatch):
    monkeypatch.setattr(finder, "SCRIPT_LINKS_MAX", 3)
    text = " ".join(f'"/jobs/{n}"' for n in range(10))
    assert len(finder._script_links(START, text)) == 3


# The routes rule (B27), the accept and refuse table: (address, typed by the agent, route).
HOME_SET = {START}
ROUTE_TABLE = [
    # search addresses: a query naming what or where, with a value, or a saved title's words
    (START + "search?q=robot", True, "search_address"),
    (START + "search?keywords=data", True, "search_address"),
    (START + "jobs?query=x&page=2", True, "search_address"),
    (START + "emplois?motcle=soudeur", True, "search_address"),
    (START + "stellen?was=x&wo=Lyon", True, "search_address"),
    (START + "jobs?L=Lyon", True, "search_address"),  # case aside
    (START + "jobs?location[]=Lyon", True, "search_address"),
    (START + "q-robot-l-lyon-jobs.html", True, "search_address"),  # a saved title in the path
    (START + "jobs/search?geoId=1050", True, "search_address"),
    # not a search address: no value, a parameter of no search, a click's landing
    (START + "search?q=", True, None),
    (START + "search?q=%20", True, None),
    (START + "search?page=2", True, None),
    (START + "search?utm_source=x", True, None),
    (START + "search?q=robot", False, None),  # a click's: the search box route counts it
    (START + "search?qq=robot2", True, None),
    # listing pages: a listing word last, alone or as word-..., or a parent of such pages
    (START + "fr/pages/emploi-developpeur", False, "listing"),
    (START + "jobs", True, "listing"),
    (START + "sitemap.xml", True, "listing"),
    (START + "sitemap-jobs.xml", True, "listing"),
    (START + "companies/acme", True, "listing"),
    (START + "companies/acme/jobs", True, "listing"),
    (START + "categories/data", False, "listing"),
    (START + "jobs-in-lyon", True, "listing"),
    (START + "Emplois", True, "listing"),
    # not a listing page: a posting, a word inside another, the start page and its pages
    (START + "jobs/123-engineer", True, None),
    (START + "jobs/data-analyst", True, None),
    (START + "jobsearch-tips", True, None),
    (START + "job-7001", True, None),
    (START + "blog/jobs-are-changing/comments", True, None),
    (START + "about", True, None),
    (START, True, None),
    (START + "?page=2", True, None),
    (START + "#top", True, None),
    ("mailto:x@example.org", True, None),
    ("not an address", True, None),
    ("", True, None),
]


@pytest.mark.parametrize(("url", "typed", "route"), ROUTE_TABLE)
def test_what_counts_as_a_route(url, typed, route):
    assert finder.route_of(url, HOME_SET, ["robot"], typed_address=typed) == route


def test_a_start_page_on_a_listing_path_is_no_route_nor_its_next_pages():
    home = {START + "jobs"}
    assert finder.route_of(START + "jobs", home, [], typed_address=True) is None
    assert finder.route_of(START + "jobs?page=3", home, [], typed_address=True) is None
    assert finder.route_of(START + "emplois", home, [], typed_address=True) == "listing"


def test_giving_up_is_refused_until_three_routes_were_tried():
    """Acceptance 4: report_no_postings is refused, saying what to try, until the search box,
    a search address and a listing page were tried; then it holds and ends the website."""
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        def give_up() -> None:
            answers.append(session.report_none("no_list"))

        give_up()
        await tool(session, "browser_type", ref="e1", text="robot", submit=True)
        give_up()
        await tool(session, "browser_navigate", url=BOARD)
        give_up()
        await tool(session, "browser_navigate", url=LISTING)
        give_up()
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    first, after_box, after_address, last = answers
    for refused in (first, after_box, after_address):
        assert refused[1] is True
        assert refused[0].startswith("Refused: do not give up yet.")
    assert finder.ROUTES["search_box"] in first[0]
    assert finder.ROUTES["search_box"] not in after_box[0]
    assert finder.ROUTES["search_address"] in after_box[0]
    assert finder.ROUTES["search_address"] not in after_address[0]
    assert finder.ROUTES["listing"] in after_address[0]
    assert last == ("Noted. Report any posting you opened, then stop.", False)
    assert entry["error"] == "no_job_list"
    assert entry["routes"] == ["search_box", "search_address", "listing"]


def test_one_route_three_times_is_one_route():
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        for query in ("robot", "welder", "arm"):
            site.pages[START + f"search?q={query}"] = BOARD_PAGE
            await tool(session, "browser_navigate", url=START + f"search?q={query}")
        answers.append(session.report_none("no_match"))
        return finder.Ran()

    run(selected(), site, script)
    assert answers[0][1] is True


@pytest.mark.parametrize(
    ("reason", "setup", "accepted"),
    [
        ("security_check", "asked", True),
        ("signin_wall", "asked", True),
        ("cookie_wall", "asked", True),
        ("security_check", "", False),  # named, not seen by code
        ("signin_wall", "refused", False),
        ("app_refused", "refused", True),
        ("app_refused", "", False),
        ("app_refused", "asked", False),
        ("no_match", "asked", False),
        ("no_list", "refused", False),
        ("no_search_control", "two_routes", True),
        ("no_search_control", "", False),
        ("no_match", "two_routes", False),
        ("no_match", "posting", True),
    ],
)
def test_a_block_code_saw_lets_the_agent_give_up(reason, setup, accepted):
    session = SimpleNamespace(
        found=[],
        verified_count=1 if setup == "posting" else 0,
        asked=1 if setup == "asked" else 0,
        refused={"unsupported_interaction": 1 if setup == "refused" else 0}
        | {"other_host": 0, "not_an_address": 0},
        routes=dict.fromkeys(["search_address", "listing"] if setup == "two_routes" else []),
    )
    assert (finder.FinderSession.routes_left(session, reason) == []) is accepted  # type: ignore[arg-type]


def test_asking_the_person_is_a_block_the_agent_may_name():
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        await tool(session, "browser_ask_person", reason="Please solve the security check")
        answers.append(session.report_none("security_check"))
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert answers == [("Noted. Report any posting you opened, then stop.", False)]
    assert entry["error"] == "security_check"


def test_a_read_page_is_parsed_off_the_event_loop(monkeypatch):
    """#797 item 1: `_read`'s parse of a kept page (up to PAGE_MAX) runs in a thread, never in
    the worker's event loop."""
    threads: list[tuple[int, int]] = []
    parsed = finder._parsed

    def watched(url: str, text: str):
        threads.append((threading.get_ident(), loop_thread[0]))
        return parsed(url, text)

    loop_thread = [0]
    monkeypatch.setattr(finder, "_parsed", watched)
    monkeypatch.setattr(finder, "PARSE_INLINE_MAX", 0)  # every page counts as a large one
    site = board()

    async def script(session):
        loop_thread[0] = threading.get_ident()
        threads.clear()  # the start page, read before the agent ran
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_snapshot")
        await tool(session, "browser_type", ref="e1", text="robot", submit=True)
        return finder.Ran()

    run(selected(), site, script)
    assert len(threads) == 3
    assert all(worker != loop for worker, loop in threads)


def test_the_agent_runs_sonnet_at_medium_effort():
    """Acceptance 5 (owner B20: the model stays Sonnet)."""
    assert finder.INTERACTIVE_FINDER.effort == "medium"
    assert finder.INTERACTIVE_FINDER.model == "claude-sonnet-5-5"
    assert "Method" in finder.INTERACTIVE_PROMPT
    assert "DOMParser" in finder.INTERACTIVE_PROMPT
    assert "listing pages" in finder.INTERACTIVE_PROMPT


def test_a_script_the_line_refuses_is_told_and_counted():
    site = board(evaluated={"ok": False, "error": "line", "message": "Refused: the line."})
    seen: dict[str, Any] = {}

    async def script(session):
        seen["answer"] = await tool(session, "browser_evaluate", function="() => 1")
        seen["refused"] = session.refused["unsupported_interaction"]
        return finder.Ran()

    run(selected(), site, script)
    assert seen["answer"]["is_error"] is True
    assert json.loads(text_of(seen["answer"]))["error"] == "line"
    assert seen["refused"] == 1


# browserd's limits of browser_evaluate: a discovery's lease (B27), else One click Apply's agent.
class Page:
    def __init__(self, result: Any = None, hang: bool = False) -> None:
        self.result, self.hang, self.scripts = result, hang, []
        self.url = START

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        self.scripts.append(arg)
        if self.hang:
            await asyncio.Event().wait()
        return self.result


def evaluating(page: Page, *, discovery: bool) -> Any:
    async def got() -> Page:
        return page

    async def nothing() -> None:
        return None

    async def settle(answer: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "url": START}

    return SimpleNamespace(
        page=got,
        discovery=discovery,
        agent=not discovery,
        gate=True,
        pacer=SimpleNamespace(dwell=nothing),
        require_input=lambda: None,
        settle=settle,
        lease_tag="lease-b27",
    )


@pytest.fixture
def here(monkeypatch):
    async def _here(tab):
        return {"ok": True}

    monkeypatch.setattr(actions, "_here", _here)


@pytest.mark.parametrize(
    ("discovery", "size", "ok"),
    [
        (True, 40_000, True),
        (True, 40_001, False),
        (True, 10_001, True),
        (False, 10_000, True),
        (False, 10_001, False),
    ],
)
def test_browserd_takes_a_longer_script_on_a_discovery_lease_only(here, discovery, size, ok):
    page = Page(json.dumps("done"))
    answer = asyncio.run(
        actions.evaluate(evaluating(page, discovery=discovery), {"function": "x" * size})
    )
    assert answer["ok"] is ok, answer
    if not ok:
        assert answer["error"] == "bad_args"


@pytest.mark.parametrize(
    ("discovery", "most"), [(True, actions.DISCOVERY_RESULT_MAX), (False, actions.RESULT_MAX)]
)
def test_browserd_cuts_a_script_result_at_the_lease_s_own_ceiling(here, discovery, most):
    page = Page("y" * 600_000)
    answer = asyncio.run(actions.evaluate(evaluating(page, discovery=discovery), {"function": "f"}))
    assert len(answer["result"]) == most
    assert answer["cut"] is True


def test_browserd_runs_a_discovery_s_script_inside_the_line_s_guard_until_its_own_timeout(
    here, monkeypatch
):
    monkeypatch.setattr(actions, "DISCOVERY_SCRIPT_S", 0)
    page = Page(hang=True)
    answer = asyncio.run(actions.evaluate(evaluating(page, discovery=True), {"function": "f"}))
    assert answer["error"] == "timeout"
    assert page.scripts == [["f", actions.line.SCRIPT_RULES]]  # SCRIPT_GUARD_JS's arguments


def test_the_tool_schemas_carry_the_script_limits():
    from app.browser import finder_tools

    schema = finder_tools.SCHEMAS["browser_evaluate"]["properties"]["function"]
    assert schema["maxLength"] == finder.SCRIPT_MAX == actions.DISCOVERY_SCRIPT_MAX
    assert finder.SCRIPT_RESULT_MAX == actions.DISCOVERY_RESULT_MAX
    for name in ("browser_find", "browser_snapshot"):
        assert finder_tools.SCHEMAS[name]["properties"]["source"]["enum"] == ["page", "script"]
    assert RESULTS  # the harness's results page stays the search box's landing


def test_a_small_page_is_parsed_in_place_and_a_large_one_in_a_thread(monkeypatch):
    threads: list[bool] = []
    parsed = finder._parsed

    def watched(url: str, text: str):
        threads.append(threading.current_thread() is threading.main_thread())
        return parsed(url, text)

    monkeypatch.setattr(finder, "_parsed", watched)
    large = "\n".join(f'- link "Robot {n}" [ref=e{n}]' for n in range(5_000))
    assert len(large) > finder.PARSE_INLINE_MAX
    site = board()
    site.pages[LISTING] = large

    async def script(session):
        threads.clear()
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_navigate", url=LISTING)
        return finder.Ran()

    run(selected(), site, script)
    assert threads == [True, False]
