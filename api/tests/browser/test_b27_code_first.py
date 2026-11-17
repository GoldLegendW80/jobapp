"""B27 (owner, 2026-11-17: "let the agent write code in a sandbox or whatever"): the website
search agent reads every result card with one script, its result kept and searchable as a page
is; the app opens and verifies a posting the agent saw and did not open; the agent cannot give
up before it tried the search box, a search address and the listing pages (a block it asked
the person about spares the search box only); browserd's script limits rise for a discovery's
lease only; a page is parsed off the event loop (#797).

The harness is test_b20_agent's (browserd's answers from `Site`, the registered tools called the
way the SDK calls them). Invented hosts only, never a real job website.
"""

import asyncio
import json
import sys
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from app.browser.client import BrowserError
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


TWO = [
    {"title": "Robot Arm Engineer", "url": SEEN_1, "text": "A card."},
    {"title": "Robot Welder", "url": SEEN_2, "text": "A card."},
]


def opening(site: TabSite, answer: Any) -> None:
    """browserd's answer to a browser_tabs call or a navigate of the app's tab: `answer(name,
    args)` is a dict to answer, an exception to raise, or None for the site's own."""
    call = site.call

    async def answered(name: str, args: dict[str, Any]) -> dict[str, Any]:
        given = answer(name, args)
        if given is None:
            return await call(name, args)
        site.calls.append((name, {k: v for k, v in args.items() if not k.startswith("_")}))
        if isinstance(given, Exception):
            raise given
        return given

    site.call = answered  # type: ignore[method-assign]


def report_two(site: TabSite, answer: Any) -> tuple[dict[str, Any], Any, list[Any]]:
    """The agent saw both postings by script and reports them; browserd answers as `answer`
    once the report starts. What the session ended with, what the report answered and what
    was stored."""
    seen: dict[str, Any] = {}

    async def script(session):
        batches = await stored(session)
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_evaluate", function="() => 1")
        opening(site, answer)
        seen["answer"] = text_of(await tool(session, "report_postings", postings=TWO))
        seen["stopped"], seen["batches"] = session.stopped, batches
        seen["opened_by_app"] = session.opened_by_app
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    return entry, seen, [f.url for batch in seen["batches"] for f in batch.findings]


def tabs_of(site: TabSite) -> list[dict[str, Any]]:
    return [args for name, args in site.calls if name == "browser_tabs"]


@pytest.mark.parametrize(
    "failure",
    [
        ("close", {"ok": False, "error": "no_tab"}),
        ("select", {"ok": False, "error": "no_tab"}),
        ("close", BrowserError("unavailable")),
        ("select", BrowserError("refused")),
    ],
)
def test_a_tab_the_app_cannot_give_back_stops_the_website(failure):
    """The app's tab not closed or the agent's not selected again: the agent's next action
    would land elsewhere, so the website stops `guard_failed` and the report keeps nothing
    more (the second posting is never opened)."""
    action, given = failure
    site = board(evaluated=scripted(CARDS))

    def answer(name, args):
        return given if name == "browser_tabs" and args["action"] == action else None

    entry, seen, urls = report_two(site, answer)
    assert seen["stopped"] == finder.GUARD_FAILED
    assert seen["answer"].startswith("Stopped: guard_failed.")
    assert urls == []
    assert seen["opened_by_app"] == 0
    assert [t["action"] for t in tabs_of(site)].count("new") == 1
    assert entry["error"] == finder.GUARD_FAILED


def test_browserd_guard_failed_on_the_apps_navigate_stops_the_website():
    """browserd could not take a page off the list back (round 2 of PR 720): the website stops
    `guard_failed` as for the agent's own navigate, after the app's tab is given back."""
    site = board(evaluated=scripted(CARDS))

    def answer(name, args):
        if name == "browser_navigate" and args["url"] == SEEN_1:
            return {"ok": False, "error": finder.GUARD_FAILED}
        return None

    _, seen, urls = report_two(site, answer)
    assert seen["stopped"] == finder.GUARD_FAILED
    assert urls == []
    assert tabs_of(site) == [
        {"action": "new"},
        {"action": "close", "index": 1},
        {"action": "select", "index": 0},
    ]


def test_a_lease_ended_during_the_apps_open_stops_with_its_own_code():
    """The lease ends while the app's tab loads: every later call fails too, and the website
    stops `lease_ended`, never `guard_failed` from the failed give back."""
    site = board(evaluated=scripted(CARDS))
    ended = [False]

    def answer(name, args):
        if name == "browser_navigate" and args["url"] == SEEN_1:
            ended[0] = True
        return BrowserError("ended") if ended[0] else None

    entry, seen, urls = report_two(site, answer)
    assert seen["stopped"] == "lease_ended"
    assert urls == []
    assert entry["error"] == "lease_ended"


@pytest.mark.parametrize("code", ["time_budget", "lease_budget", "permission_unavailable"])
def test_a_budget_spent_during_the_apps_open_stops_with_its_own_code(code, monkeypatch):
    from app.browser.sources import Pages, SourceError

    site = board(evaluated=scripted(CARDS))
    act = Pages._act

    async def spent(self, action, tool="browser_navigate"):
        if self.tools is not None and any(n == "browser_tabs" for n, _ in site.calls):
            raise SourceError(code)
        return await act(self, action, tool)

    monkeypatch.setattr(Pages, "_act", spent)
    _, seen, urls = report_two(site, lambda name, args: None)
    assert seen["stopped"] == code
    assert urls == []
    assert tabs_of(site)[-2:] == [{"action": "close", "index": 1}, {"action": "select", "index": 0}]


def test_an_error_status_at_the_reported_address_is_refused():
    """A 404 on the app's open: nothing read as a posting, `opened_by_app` unchanged, the
    report refused and the website goes on."""
    site = board(evaluated=scripted(CARDS))

    def answer(name, args):
        if name == "browser_navigate" and args["url"] == SEEN_1:
            site.current = site.tabs[site.at] = SEEN_1
            return {"ok": True, "url": SEEN_1, "status": 404}
        return None

    _, seen, urls = report_two(site, answer)
    report = json.loads(seen["answer"])
    assert report["kept"] == 1
    assert [r["reason"] for r in report["refused"]] == ["detail_not_opened"]
    assert urls == [SEEN_2]
    assert seen["opened_by_app"] == 1
    assert seen["stopped"] == ""


@pytest.mark.parametrize(
    "landed", [START + "jobs/7001-robot-arm-engineer", "https://ats.example.org/jobs/7001"]
)
def test_a_redirected_open_stores_the_landed_address(landed):
    """The reported address redirects, on its website or to another (an ATS): the finding
    is stored with the address the page landed on."""
    site = board(evaluated=scripted(CARDS))
    site.pages[landed] = POSTING_1

    def answer(name, args):
        if name == "browser_navigate" and args["url"] == SEEN_1:
            site.current = site.tabs[site.at] = landed
            return {"ok": True, "url": landed}
        return None

    _, seen, urls = report_two(site, answer)
    assert urls == [landed, SEEN_2]
    assert seen["opened_by_app"] == 2


def test_a_new_tab_browserd_does_not_name_is_given_back_and_nothing_opened():
    """browserd made a tab but names none current: the agent's tab is selected again, no
    address is opened in a tab that may be the agent's, the website goes on."""
    site = board(evaluated=scripted(CARDS))

    def answer(name, args):
        if name == "browser_tabs" and args["action"] == "new":
            return {"ok": True, "tabs": [{"index": 0, "url": BOARD, "current": False}]}
        return None

    _, seen, urls = report_two(site, answer)
    assert urls == []
    assert seen["stopped"] == ""
    assert {"action": "select", "index": 0} in tabs_of(site)
    assert not any(t["action"] == "close" for t in tabs_of(site))
    assert [
        n for n, a in site.calls if n == "browser_navigate" and a["url"] in (SEEN_1, SEEN_2)
    ] == []


def test_the_app_opens_at_most_twenty_postings_per_call():
    links = [START + f"jobs/{8000 + i}" for i in range(finder.APP_OPENS_MAX + 1)]
    cards = [{"title": f"Robot Role {i}", "link": link} for i, link in enumerate(links)]
    site = board(evaluated=scripted(cards))
    for i, link in enumerate(links):
        site.pages[link] = f'- heading "Robot Role {i}" [level=1]\n- paragraph: Invented.'
    postings = [
        {"title": f"Robot Role {i}", "url": u, "text": "A card."} for i, u in enumerate(links)
    ]
    seen: dict[str, Any] = {}

    async def script(session):
        batches = await stored(session)
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_evaluate", function="() => 1")
        seen["report"] = report_of(await tool(session, "report_postings", postings=postings))
        seen["batches"] = batches
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert seen["report"]["kept"] == finder.APP_OPENS_MAX
    assert seen["report"]["refused"] == [
        {"index": 20, "reason": "app_opens_max", "do": finder.REPORT_ADVICE["app_opens_max"]}
    ]
    assert [t["action"] for t in tabs_of(site)].count("new") == finder.APP_OPENS_MAX
    assert entry["opened_by_app"] == finder.APP_OPENS_MAX


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


@pytest.mark.parametrize(
    ("raw", "cut"),
    [
        (json.dumps("short"), False),  # a JSON string loses its quotes, nothing is cut
        (json.dumps("x" * 50_000), False),
        (json.dumps({"a": [1, 2]}), False),
        (json.dumps("y\n" * 300_000), True),  # past SCRIPT_RESULT_MAX once read
        ("[" * 5_000 + "]" * 5_000, False),  # nested past the parser: kept as answered
    ],
)
def test_a_script_result_is_said_cut_only_when_it_was(raw, cut):
    """Review of B27: the kept result is cut only when browserd or SCRIPT_RESULT_MAX cut it,
    and a result nested deeper than the parser takes is kept as text, never an error."""

    async def kept() -> finder.Kept:
        session = object.__new__(finder.FinderSession)
        session.script, session.script_links = None, {}
        script, _ = await finder.FinderSession._keep_script(session, START, raw, False)
        return script

    script = asyncio.run(kept())
    assert script.cut is cut
    assert len(script.text) <= finder.SCRIPT_RESULT_MAX


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


ROUTES_2 = {"search_address", "listing"}
ROUTES_3 = {"search_box", *ROUTES_2}


# The accept and refuse table of the blocks (review of #800): code sees the ask and the app's
# refusals, never the block itself, so a named block spares the search box only.
BLOCK_TABLE = [
    # asked alone, or with one route: refused, whatever the block named
    ("security_check", {"asked"}, False),
    ("signin_wall", {"asked"}, False),
    ("cookie_wall", {"asked"}, False),
    ("signin_wall", {"asked", "search_address"}, False),
    ("signin_wall", {"asked", "listing"}, False),
    ("cookie_wall", {"asked", "search_box", "listing"}, False),
    # asked and the two other routes tried: accepted
    ("security_check", {"asked", *ROUTES_2}, True),
    ("signin_wall", {"asked", *ROUTES_2}, True),
    ("cookie_wall", {"asked", *ROUTES_2}, True),
    # named without an ask: the three routes
    ("security_check", ROUTES_2, False),
    ("signin_wall", {"refused", *ROUTES_2}, False),  # the app's refusal is no ask
    ("security_check", ROUTES_3, True),
    # app_refused: the app's own refusals only, then the two routes
    ("app_refused", {"refused"}, False),
    ("app_refused", {"refused", "search_address"}, False),
    ("app_refused", {"refused", *ROUTES_2}, True),
    ("app_refused", {"other_host", *ROUTES_2}, False),  # the agent's slip
    ("app_refused", {"not_an_address", *ROUTES_2}, False),  # the agent's slip
    ("app_refused", {"other_host", "not_an_address"}, False),
    ("app_refused", {"asked", *ROUTES_2}, False),
    ("app_refused", ROUTES_2, False),
    ("app_refused", ROUTES_3, True),
    # the other reasons: an ask or a refusal spares nothing
    ("no_match", {"asked", *ROUTES_2}, False),
    ("no_list", {"refused", *ROUTES_2}, False),
    ("no_search_control", ROUTES_2, True),
    ("no_search_control", set(), False),
    ("no_search_control", {"search_address"}, False),
    ("no_match", ROUTES_2, False),
    ("no_match", ROUTES_3, True),
    ("no_match", {"posting"}, True),
]


@pytest.mark.parametrize(("reason", "setup", "accepted"), BLOCK_TABLE)
def test_a_block_spares_the_search_box_only(reason, setup, accepted):
    session = SimpleNamespace(
        found=[],
        verified_count=1 if "posting" in setup else 0,
        asked=1 if "asked" in setup else 0,
        refused=Counter(
            {
                "unsupported_interaction": int("refused" in setup),
                "other_host": int("other_host" in setup),
                "not_an_address": int("not_an_address" in setup),
            }
        ),
        routes=dict.fromkeys(r for r in finder.ROUTES if r in setup),
    )
    assert (finder.FinderSession.routes_left(session, reason) == []) is accepted  # type: ignore[arg-type]


def refusing_navigations(site: TabSite, answer: dict[str, Any]) -> None:
    """From now on, browserd answers every browser_navigate with `answer` (its tab moves not)."""
    call = site.call

    async def refusing(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_navigate":
            args.pop("_discovery"), args.pop("_discovery_scope")
            site.calls.append((name, dict(args)))
            return answer
        return await call(name, args)

    site.call = refusing  # type: ignore[method-assign]


def test_asking_the_person_spares_the_search_box_only():
    """The owner's audit case: a sign in wall on the search, asked about. Giving up is refused
    until the search address and the listing pages were tried, and the answer names them."""
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        await tool(session, "browser_ask_person", reason="Please sign in to the website")
        answers.append(session.report_none("signin_wall"))
        await tool(session, "browser_navigate", url=BOARD)
        answers.append(session.report_none("signin_wall"))
        await tool(session, "browser_navigate", url=LISTING)
        answers.append(session.report_none("signin_wall"))
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    first, second, last = answers
    assert first[1] is True
    assert second[1] is True
    assert finder.ROUTES["search_box"] not in first[0]
    assert finder.ROUTES["search_address"] in first[0]
    assert finder.ROUTES["listing"] in first[0]
    assert finder.ROUTES["search_address"] not in second[0]
    assert finder.ROUTES["listing"] in second[0]
    assert last == ("Noted. Report any posting you opened, then stop.", False)
    assert entry["error"] == "signin_wall"


@pytest.mark.parametrize("code", sorted(finder.APP_REFUSED))
def test_a_website_the_app_refuses_everywhere_ends_after_two_navigations(code):
    """Every navigate refused by browserd: each refused address counts as the route it asked
    for, so `app_refused` holds after a search address and a listing page, never before."""
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        refusing_navigations(site, {"ok": False, "error": code})
        answers.append(session.report_none("app_refused"))
        said = text_of(await tool(session, "browser_navigate", url=BOARD))
        assert said == finder.NAVIGATION_REFUSED
        answers.append(session.report_none("app_refused"))
        await tool(session, "browser_navigate", url=LISTING)
        answers.append(session.report_none("app_refused"))
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert [answer[1] for answer in answers] == [True, True, False]
    assert entry["routes"] == ["search_address", "listing"]


def test_a_security_check_on_every_route_ends_after_two_navigations():
    """Cloudflare on every page (an HTTP 403 with its check), asked about: the two addresses
    that answered the error count as their routes; a page that did not load counts nothing."""
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        await tool(session, "browser_ask_person", reason="Please solve the security check")
        refusing_navigations(site, {"ok": False, "error": "navigation_failed"})
        await tool(session, "browser_navigate", url=BOARD)  # did not load: no route
        answers.append(session.report_none("security_check"))
        call = site.call

        async def checked(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "browser_navigate":
                site.pages[args["url"]] = CHECK_PAGE
                site.current = args["url"]
                return {"ok": True, "url": args["url"], "status": 403}
            return await call(name, args)

        site.call = checked  # type: ignore[method-assign]
        await tool(session, "browser_navigate", url=BOARD)
        answers.append(session.report_none("security_check"))
        await tool(session, "browser_navigate", url=LISTING)
        answers.append(session.report_none("security_check"))
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert [answer[1] for answer in answers] == [True, True, False]
    assert entry["error"] == "security_check"


@pytest.mark.parametrize("url", ["mailto:x@example.org", "javascript:alert(1)", "ftp://x/jobs"])
def test_the_agents_own_slips_try_no_route_and_never_unlock_app_refused(url):
    site = board()
    answers: list[tuple[str, bool]] = []

    async def script(session):
        for _ in range(3):
            await tool(session, "browser_navigate", url=url)
        answers.append(session.report_none("app_refused"))
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert answers[0][1] is True
    assert entry["routes"] == []


def test_a_read_page_is_parsed_off_the_event_loop(monkeypatch):
    """#797 item 1: `_read`'s parse of a kept page (up to PAGE_MAX), the start page's
    included, runs in a thread, never in the worker's event loop."""
    on_loop: list[bool] = []
    parsed = finder._parsed

    def watched(url: str, text: str):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return parsed(url, text)

    monkeypatch.setattr(finder, "_parsed", watched)
    monkeypatch.setattr(finder, "PARSE_INLINE_MAX", 0)  # every page counts as a large one
    site = board()
    before: list[int] = []

    async def script(session):
        before.append(len(on_loop))  # the start page, read before the agent ran
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_snapshot")
        await tool(session, "browser_type", ref="e1", text="robot", submit=True)
        return finder.Ran()

    run(selected(), site, script)
    assert before == [1]
    assert on_loop == [False] * 4


def test_a_small_page_is_parsed_in_place():
    assert asyncio.run(finder.parsed_off_loop(START, "x" * finder.PARSE_INLINE_MAX)) is None
    big = asyncio.run(finder.parsed_off_loop(START, "x" * (finder.PARSE_INLINE_MAX + 1)))
    assert big is not None


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
