# ruff: noqa: E501  tables of addresses and names, one per line
"""Find jobs on LinkedIn, round 3 (sweep a14e7b2b of 2026-10-30, 16:08 UTC on the Jetson, main
ea987459), and the follow ups of the review of PR 730.

1. Two result links (`signed_in/link`, names of 227 and 188 characters) and a navigation to
   `/jobs/search-results/` were refused. browserd already took `/jobs/search-results/` as a
   result list (the same query rules as `/jobs/search/`); its warnings now say which part of the
   address refused it (discovery.why_refused) and the query's keys (logged_keys), never a value.
   A LinkedIn link naming the posting it selects (`currentJobId`) makes that posting's own page
   observed (finder.FinderSession._read), and a refused click or navigation on it tells the
   agent that page. A link's long text holding "message" or "chat" in a company name is no
   composer: links are judged by their address alone.
2. The place box's clear button ("Effacer le lieu", a native `<button type=button>` refused by
   the danger detector) is clicked signed in, and only it (discovery.place_clear).
3. The place box was covered (`TimeoutError reason=covered`): a covered search box is focused
   and typed in signed in (actions.type_text), else the refusal says what to click first.
4. LinkedIn ran first with a third of the sweep (300 s) at 10 to 30 s an action: a website with
   its own pace now runs last (interactive.run_order), and the agent is told to run every title's
   search first.
Follow ups of the review of PR 730: SIGNED_IN_JS's console lines name only its own APIs and the
page's own host; the interstitial's `navigator.serviceWorker` is a stand in (its text checked
here, its behaviour in real Chromium by a throwaway probe); the Worker gate captures what it
reads; a consent form never posts to a subscribe, save or follow address; a refusal's warning
reads the decline with the page's address.

Only the Playwright and CDP boundary is fake; no real site is requested. Names, ids and people
are invented.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from app.browser.sources import SourceError
from app.browser.sources import interactive as interactive_source
from tests.browser import test_b17_browserd as b17
from tests.browser.test_b19_search import control
from tests.browser.test_find_jobs_round_2 import banner_button
from tests.browser.test_j18_scope import discovery
from tests.browser.test_three_sites import LINKEDIN, RESULTS, scope, signed_in_tab

actions = b17.actions

SEARCH_RESULTS = (
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4012345678&keywords=genai%20engineer"
    "&origin=JOB_SEARCH_PAGE_JOB_FILTER&geoId=92000000&eBP=NOT_ELIGIBLE_FOR_CHARGING"
    "&refId=Zm9vYmFyYmF6cXV4MTIzNA%3D%3D&trackingId=cXV4YmF6YmFyZm9vNDMyMQ%3D%3D"  # gitleaks:allow
)
UNSAVED = (
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4012345678"
    "&keywords=Jane%20Roe%20e2e%20secret&origin=JOB_SEARCH_PAGE_JOB_FILTER&e2eKey=1"
)
VIEW = "https://www.linkedin.com/jobs/view/4012345678/"


def judged(shape, tool="browser_click", text=None, **kw):
    args = {} if text is None else {"text": text}
    return discovery.judged(shape, tool, RESULTS, scope=scope(**kw), args=args)


# --- 1. LinkedIn's `/jobs/search-results/` ------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "opens"),
    [
        ("https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer", True),
        ("https://www.linkedin.com/jobs/search-results?keywords=GenAI+Engineer", True),
        (SEARCH_RESULTS, True),
        ("https://www.linkedin.com/jobs/search-results/?currentJobId=4012345678", True),
        (
            "https://www.linkedin.com/jobs/search-results/?keywords=solution%20architect&location=Worldwide",
            True,
        ),
        # The same query rules as /jobs/search/: a saved title or nothing in the search key.
        (UNSAVED, False),
        ("https://www.linkedin.com/jobs/search-results/?keywords=data%20engineer", False),
        (
            "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer&action=apply",
            False,
        ),
        ("https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer#x", False),
        # Any other key of a result list goes, as on /jobs/search/ (tracking, filters).
        (
            "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer&f_AL=true&e2eKey=1",
            True,
        ),
    ],
)
def test_linkedin_s_search_results_list_opens_as_a_result_list(url, opens):
    assert discovery.signed_request(url, "GET", True, scope()) is opens


@pytest.mark.parametrize(
    ("url", "listed"),
    [
        ("https://www.linkedin.com/jobs/search-results/", True),
        ("https://www.linkedin.com/jobs/search-results", True),
        ("https://www.linkedin.com/jobs/search/", True),
        ("https://fr.linkedin.com/jobs/collections/recommended/", True),
        ("http://www.linkedin.test:8080/jobs/search-results/", True),
        ("https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer", False),
        ("https://www.linkedin.com/jobs/search-results/extra/", False),
        ("https://www.linkedin.com/Jobs/search-results/", False),
        ("https://www.linkedin.com/jobs/collections/a%2Fb/", False),
        ("https://fr.indeed.com/jobs/search-results/", False),
        ("https://www.welcometothejungle.com/jobs/search/", False),
    ],
)
def test_the_api_opens_a_linkedin_result_list_with_no_query_as_browserd_does(url, listed):
    assert finder.result_list(url) is listed
    if listed:  # browserd opens the same address (discovery.listing)
        start = "http://www.linkedin.test:8080/" if ".test" in url else LINKEDIN
        assert discovery.signed_request(url, "GET", True, scope(start))


def test_a_result_list_with_no_query_is_navigated_to_unobserved():
    one = session()
    one.pages = RefusingPages("host_not_allowed")
    asyncio.run(one.navigate("/jobs/search-results/"))
    assert one.pages.calls == [
        ("browser_navigate", "https://www.linkedin.com/jobs/search-results/")
    ]
    answer = asyncio.run(one.navigate("/jobs/search-results/?keywords=genai%20engineer"))
    assert answer == (finder.UNOBSERVED_SIGNED_IN, True)  # with a query: only from a link


def test_a_worldwide_place_in_search_results_needs_no_place_limit():
    url = (
        "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer&location=Worldwide"
    )
    assert discovery.signed_request(url, "GET", True, scope())
    assert not discovery.signed_request(url, "GET", True, scope(places=("Lyon",)))


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (SEARCH_RESULTS, "opens"),
        (VIEW, "opens"),
        (UNSAVED, "unsaved_title"),
        ("https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer#top", "fragment"),
        ("https://www.linkedin.com/in/jane-roe-e2e/", "personal"),
        ("https://www.linkedin.com/messaging/thread/2-e2e/", "personal"),
        (
            "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer&do=easyApply",
            "destructive",
        ),
        ("https://www.linkedin.com/company/acme/jobs/", "not_listing"),
        ("https://elsewhere.example.test/jobs", "off_family"),
        ("https://www.linkedin.com/jobs/search-results/?keywords=" + "a" * 2000, "too_long"),
    ],
)
def test_why_an_address_is_refused_is_said_in_the_code_s_words(url, why):
    assert discovery.why_refused(url, scope()) == why


def test_a_worldwide_place_with_specific_places_says_so():
    url = (
        "https://www.linkedin.com/jobs/search-results/?keywords=genai%20engineer&location=Worldwide"
    )
    assert discovery.why_refused(url, scope(places=("Lyon",))) == "worldwide"


def test_only_the_known_query_keys_are_named():
    assert discovery.logged_keys(UNSAVED) == "currentJobId,keywords,origin,+1"
    assert discovery.logged_keys("https://www.linkedin.com/jobs/") == "-"
    assert discovery.logged_keys("https://x.test/?jane=1&roe=2") == "+2"
    assert discovery.logged_keys(None) == "unreadable"


def card_link(href: str, *names: str) -> dict[str, Any]:
    return control(*names, tag="a", kind="", href=href, in_form=False)


# A card's link as long as the sweep's (227 characters), its company name holding words a
# composer's names hold: a link is judged by its address alone.
LONG_NAMES = (
    "GenAI Engineer\nGenAI Engineer with verification\nMessageBird Chat Note Labs\n"
    "Bordeaux, Nouvelle-Aquitaine, France (Hybride)\nIl y a 2 jours\nPromu(e)\n"
    "Comment les équipes de données travaillent ici, voir l'offre complète"
)


def test_a_result_link_whose_text_holds_message_words_is_judged_by_its_address():
    assert discovery.composes({"names": [LONG_NAMES]})
    link = card_link(SEARCH_RESULTS, LONG_NAMES)
    assert judged(link) == (None, "point:signed_in")
    assert judged(card_link(VIEW, LONG_NAMES)) == (None, "point:signed_in")
    # A real messaging link stays refused, whatever its words.
    messaging = card_link("https://www.linkedin.com/messaging/thread/2-e2e/", "Jobs")
    assert judged(messaging) == (discovery.UNSUPPORTED, "signed_in/link")


def test_a_refused_link_s_warning_says_why_and_its_keys_never_its_address(caplog):
    found = signed_in_tab()
    found.lease_tag = "e2e-l3"
    with caplog.at_level(logging.WARNING, logger="browserd"):
        assert found.judge(card_link(UNSAVED, LONG_NAMES), "click", "browser_click")
    [line] = [r.getMessage() for r in caplog.records]
    assert "rule=signed_in/link tag=a" in line
    assert line.endswith("why=unsaved_title keys=currentJobId,keywords,origin,+1")
    for word in ("Jane", "secret", "e2eKey", "4012345678", "JOB_SEARCH", "MessageBird"):
        assert word not in line


def test_a_refused_document_s_stop_says_why_and_its_keys():
    found = signed_in_tab()
    route = b17.Route()
    asyncio.run(found._signed_route(route, b17.request(UNSAVED, "GET", "main")))
    assert route.outcome == "blockedbyclient"
    assert found.state.stopped_why == "why=unsaved_title keys=currentJobId,keywords,origin,+1"
    found.state.end()
    assert found.state.stopped_why == ""


def test_the_stop_s_warning_carries_the_reason(caplog):
    found = signed_in_tab()
    found.lease_tag = "e2e-l3"
    page = found.state.page
    page.url = RESULTS
    found.state.native_context = SimpleNamespace(pages=[page])
    found.state.blocked.append(UNSAVED[:200])
    found.state.stopped_kind, found.state.stopped_why = "document", "why=unsaved_title keys=-"
    found.on_list = lambda page: True
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result = asyncio.run(found.settle({"ok": True}))
    assert result["error"] == "host_not_allowed"
    [line] = [r.getMessage() for r in caplog.records]
    assert line.endswith(
        "a navigation outside the allow list stopped: host=www.linkedin.com kind=document "
        "why=unsaved_title keys=-"
    )
    assert found.state.stopped_why == ""


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        (SEARCH_RESULTS, VIEW),
        (UNSAVED, VIEW),
        (
            "https://www.linkedin.com/jobs/search-results/?currentJobId=12&keywords=x",
            "https://www.linkedin.com/jobs/view/12/",
        ),
        ("https://www.linkedin.com/jobs/search-results/?currentJobId=12&currentJobId=13", ""),
        ("https://www.linkedin.com/jobs/search-results/?currentJobId=12a", ""),
    ],
)
def test_the_posting_a_search_results_address_shows(url, shown):
    assert finder.shown_posting(url) == shown


def session() -> finder.FinderSession:
    plain = finder.InteractiveScope(LINKEDIN, ("genai engineer",), ("everywhere",), signed_in=True)
    return finder.FinderSession(None, LINKEDIN, "", "e2e-round3", interactive=plain)


LIST = (
    '- list "Résultats" [ref=e30]:\n'
    "  - listitem [ref=e31]:\n"
    f'    - link "GenAI Engineer Acme Robotics" [ref=e32]:\n      - /url: {UNSAVED}\n'
    "  - listitem [ref=e33]:\n"
    '    - link "Data Engineer Example Labs" [ref=e34]:\n'
    "      - /url: https://www.linkedin.com/jobs/search-results/?currentJobId=4099999999\n"
    '  - link "Acme Robotics" [ref=e35]:\n'
    "    - /url: https://www.linkedin.com/company/acme/\n"
)


def test_a_link_selecting_a_posting_makes_its_own_page_observed():
    one = session()
    one._read(RESULTS, LIST)
    assert VIEW in one.seen
    assert "https://www.linkedin.com/jobs/view/4099999999/" in one.seen
    assert one.selects == {
        "e32": VIEW,
        "e34": "https://www.linkedin.com/jobs/view/4099999999/",
    }
    one._read(VIEW, "")  # the next page read forgets the refs, never the observed addresses
    assert one.selects == {}
    assert VIEW in one.seen


class RefusingPages:
    """Pages whose browserd refuses every action with `error`."""

    def __init__(self, error: str) -> None:
        self.error, self.calls = error, []
        self.tools = SimpleNamespace(call=self.call)
        self.last_url, self.last_text = RESULTS, ""

    async def call(self, name, args):
        self.calls.append((name, args.get("url") or args.get("ref")))
        return {"ok": False, "error": self.error}

    async def _act(self, action, tool="browser_navigate"):
        await action()
        raise SourceError("page_failed")


def test_a_refused_click_on_such_a_link_tells_the_posting_s_page():
    one = session()
    one._read(RESULTS, LIST)
    one.pages = RefusingPages(discovery.UNSUPPORTED)
    answer, failed = asyncio.run(one.interact("browser_click", {"ref": "e32"}))
    assert failed
    assert answer == finder.SELECTS_POSTING.format(url=VIEW)
    assert one.refused["unsupported_interaction"] == 1
    # Any other refused click answers as before.
    answer, _ = asyncio.run(one.interact("browser_click", {"ref": "e35"}))
    assert json.loads(answer) == {"error": discovery.UNSUPPORTED}


def test_a_refused_navigation_to_such_a_link_tells_the_posting_s_page():
    one = session()
    one._read(RESULTS, LIST)
    one.pages = RefusingPages("host_not_allowed")
    answer, failed = asyncio.run(one.navigate(UNSAVED))
    assert (answer, failed) == (finder.SELECTS_POSTING.format(url=VIEW), True)
    answer, _ = asyncio.run(one.navigate("https://www.linkedin.com/company/acme/"))
    assert answer == finder.NAVIGATION_REFUSED
    assert "Jane" not in finder.SELECTS_POSTING.format(url=VIEW)


# --- 2. the place box's clear button ------------------------------------------------------------


def clear_button(*names: str, **kw: Any) -> dict[str, Any]:
    return control(*names, **{"tag": "button", "kind": "button", "in_form": False, **kw})


# As the run's warning described it: a native button of type button, box kind place, its first
# name 15 characters, refused by the danger detector.
EFFACER = clear_button("Effacer le lieu")


def test_the_place_box_s_clear_button_is_clicked_signed_in():
    assert len(EFFACER["names"][0]) == 15
    assert discovery.box_kind(EFFACER["names"]) == "place"
    assert actions.detect.verdict(EFFACER, action="click") == "danger"
    assert judged(EFFACER) == (None, "point:place_clear")
    # With specific places saved too: clearing a place is always allowed.
    assert judged(EFFACER, places=("Lyon",)) == (None, "point:place_clear")


@pytest.mark.parametrize(
    "shape",
    [
        clear_button("Clear location"),
        clear_button("Effacer la saisie du lieu", "jobs-search-box-location-clear-ember31"),
        clear_button("", "Effacer le lieu", "ember42"),
        clear_button("Vider le champ ville"),
        clear_button("Clear", "Location"),
        control("Effacer le lieu", tag="div", kind="", role="button", in_form=False),
        clear_button(
            "Effacer le lieu", in_form=True, method="get",
            action="https://www.linkedin.com/jobs/search-results/",
        ),
    ],
)  # fmt: skip
def test_a_place_box_s_clear_button_reads_in_several_ways(shape):
    assert judged(shape)[0] is None


@pytest.mark.parametrize(
    ("shape", "code"),
    [
        (clear_button("Effacer"), "danger"),  # no place word: no place box's
        (clear_button("Effacer tout"), "danger"),
        (clear_button("Effacer le mot-clé"), "danger"),  # a title box's
        (clear_button("Effacer les filtres du lieu", "Effacer l'historique"), "danger"),
        (clear_button("Supprimer le lieu"), "danger"),
        (clear_button("Effacer le lieu", "Supprimer"), "danger"),
        (clear_button("Clear all filters"), discovery.UNSUPPORTED),
        (clear_button("Reset location"), discovery.UNSUPPORTED),
        (clear_button("Effacer le lieu et postuler"), "danger"),
        (clear_button("Effacer le lieu", "btnFollowLocation"), "danger"),
        (clear_button("Effacer le lieu", "Bordeaux"), "danger"),
        (clear_button("Еffacer le lieu"), discovery.UNSUPPORTED),  # a Cyrillic capital Ie
        (clear_button("Effacer le lieu", kind="submit"), "danger"),
        (clear_button("Effacer le lieu", kind="reset"), "danger"),
        (clear_button("Effacer le lieu", in_control=True), "danger"),
        (clear_button("Effacer le lieu", holds_submit=True), "danger"),
        (clear_button("Effacer le lieu", in_form=True, method="post", action="https://www.linkedin.com/jobs/search-results/"), "danger"),
        (control("Effacer le lieu", tag="a", kind="", href="https://www.linkedin.com/jobs/", in_form=False), "danger"),
        (control("Effacer le lieu", tag="input", kind="button", in_form=False), "danger"),
    ],
)  # fmt: skip
def test_no_other_clear_or_delete_button_is_clicked(shape, code):
    assert judged(shape)[0] == code


def test_the_clear_button_is_refused_anonymous_and_at_another_click_point():
    assert judged(EFFACER, signed_in=False)[0] == "danger"
    over = {**EFFACER, "point": {**control("x", tag="div", kind=""), "inside": False}}
    assert judged(over)[0] == "danger"
    elsewhere = {**EFFACER, "point": {**clear_button("Effacer tout"), "inside": True}}
    assert judged(elsewhere)[0] == "danger"
    assert not discovery.place_clear(EFFACER, None)
    assert not discovery.place_clear("unreadable", scope())


# --- 3. a covered search box is focused and typed in ---------------------------------------------


class TimeoutError(Exception):  # Playwright's TimeoutError is matched by its name
    pass


PLACE_BOX = control("Ville, département ou code postal", role="combobox", in_form=False)


class Box:
    """A place box another element covers: the hover times out, focus and fill work."""

    def __init__(self, focused: bool = True) -> None:
        self.focused, self.calls = focused, []

    async def focus(self, **kw: Any) -> None:
        self.calls.append("focus")

    async def evaluate(self, script: str, arg: Any = None, **kw: Any) -> Any:
        assert script == actions.FOCUSED_JS
        return self.focused

    async def click(self, **kw: Any) -> None:
        self.calls.append("click")

    async def fill(self, value: str, **kw: Any) -> None:
        self.calls.append(f"fill:{value}")


def typed(box: Box, *, signed_in: bool = True, message: str = "x intercepts pointer events"):
    found = signed_in_tab()
    found.lease_tag = "e2e-l3"
    found.state.signed_in = signed_in
    found.state.scope = scope(signed_in=signed_in)
    keys: list[str] = []
    page = SimpleNamespace(
        keyboard=SimpleNamespace(type=lambda c: _record(keys, c), insert_text=lambda c: _record(keys, c), press=lambda k: _record(keys, f"<{k}>")),
    )  # fmt: skip

    async def element(args):
        return box, None

    async def approach(locator):
        raise TimeoutError(f"Timeout 5000ms exceeded. {message}")

    asked: list[bool] = []

    async def facts(locator, point=False):
        asked.append(point)
        return {**PLACE_BOX, "point": {**control("Bordeaux et périphérie", tag="div", kind="", role="button"), "inside": False}} if point else {k: v for k, v in PLACE_BOX.items() if k != "point"}  # fmt: skip

    async def front():
        return page

    async def settle(result):
        return result

    async def nothing(*args, **kw):
        return None

    found.element, found.approach, found.facts = element, approach, facts
    found.page, found.settle = front, settle
    found.pacer = SimpleNamespace(key_delays_s=lambda n: [0.0] * n, sleep=nothing, dwell=nothing)
    here = actions._here
    actions._here = lambda tab: _done({"ok": True, "url": RESULTS})
    try:
        result = asyncio.run(
            actions.type_text(found, {"ref": "e9", "text": "Worldwide", "submit": True})
        )
    finally:
        actions._here = here
    return result, keys, asked


async def _record(keys: list[str], value: str) -> None:
    keys.append(value)


async def _done(value: Any) -> Any:
    return value


def test_a_covered_place_box_is_focused_and_typed_in_signed_in(caplog):
    box = Box()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result, keys, asked = typed(box)
    assert result["ok"]
    assert box.calls == ["focus", "fill:"]  # never a click on what covers it
    assert "".join(k for k in keys if not k.startswith("<")) == "Worldwide"
    assert keys[-1] == "<Enter>"
    assert asked == [False]  # judged on its own facts: no click lands anywhere
    assert any("typed through the focus" in r.getMessage() for r in caplog.records)


def test_a_covered_box_that_takes_no_focus_types_nothing():
    box = Box(focused=False)
    result, keys, _ = typed(box)
    assert result == actions.failure("element_covered", actions.BOX_COVERED)
    assert keys == []
    assert "click" not in box.calls


def test_a_covered_box_anonymous_or_not_ready_says_what_to_click_first(caplog):
    box = Box()
    with caplog.at_level(logging.WARNING, logger="browserd"):
        result, keys, _ = typed(box, signed_in=False)
    assert result == actions.failure("element_covered", actions.BOX_COVERED)
    assert keys == []
    assert box.calls == []
    assert (
        caplog.records[-1]
        .getMessage()
        .endswith("browser_type not ready: reason=covered covered_by=div/button")
    )
    result, _, _ = typed(Box(), message="element is not visible")
    assert result["error"] == "element_covered"  # where the click would land is covered


def test_a_covered_box_still_takes_only_a_saved_value():
    found = signed_in_tab()
    found.lease_tag = "e2e-l3"
    alone = {k: v for k, v in PLACE_BOX.items() if k != "point"}
    assert found.judge(alone, "type", "browser_type", {"text": "Paris"})["error"]
    assert found.judge(alone, "type", "browser_type", {"text": "Worldwide"}) is None


# --- 4. the time of a sweep: a paced website runs last ---------------------------------------------


def test_a_website_with_its_own_pace_runs_after_the_others():
    sites = [
        SimpleNamespace(effective_platform="linkedin", url="l"),
        SimpleNamespace(effective_platform="indeed", url="i"),
        SimpleNamespace(effective_platform="custom", url="c"),
        SimpleNamespace(effective_platform="welcome_to_the_jungle", url="w"),
    ]
    assert [s.url for s in interactive_source.run_order(sites)] == ["i", "c", "w", "l"]
    assert interactive_source.run_order([]) == []


def test_the_agent_is_told_the_order_of_the_place_and_to_search_every_title_first():
    prompt = " ".join(finder.INTERACTIVE_PROMPT.split())
    for words in (
        "click the button showing the place it set",
        "click the box's clear button",
        "then click that suggestion in the list under the box",
        "The place stays for the next searches: set it once.",
        "run the search of every wanted title first",
        "Then open the postings that fit best",
        "a result link or card whose address holds currentJobId=<number>: open /jobs/view/<number>/ directly",
    ):
        assert words in prompt, words
    assert not any(dash in finder.INTERACTIVE_PROMPT for dash in ("–", "—", " - "))


# --- follow ups of the review of PR 730 -----------------------------------------------------------


def console(text: str, page: Any) -> Any:
    return SimpleNamespace(text=text, page=page)


def test_a_console_line_names_only_the_script_s_own_apis_and_the_page_s_host(caplog):
    found = signed_in_tab()
    found.lease_tag = "e2e-l3"
    mark = actions.SCRIPT_MARK
    page = SimpleNamespace(url="https://www.linkedin.com/jobs/search-results/?keywords=x")
    with caplog.at_level(logging.WARNING, logger="browserd"):
        found._console(console(f"{mark} blocked JeanDupont https://0612345678.example", page))
        found._console(console(f"{mark} blocked Worker https://0612345678.example", page))
        found._console(console(f"{mark} allowed register https://x.test", None))
        for api in actions.SCRIPT_APIS:
            found._console(console(f"{mark} blocked {api} https://x.test", page))
    lines = [r.getMessage() for r in caplog.records]
    assert lines[:2] == [
        "lease e2e-l3: the search's page script blocked Worker: host=www.linkedin.com count=1",
        "lease e2e-l3: the search's page script allowed register: host=unreadable count=1",
    ]
    assert len(lines) == 2 + len(actions.SCRIPT_APIS)  # Worker's 2nd time, each API once
    assert all("Jean" not in line and "0612345678" not in line for line in lines)


def test_the_interstitial_s_service_worker_is_a_stand_in():
    script = actions.SIGNED_IN_JS
    assert "return read.get.call(this)" not in script  # the real container is never returned
    assert 'tell("allowed", "serviceWorker"); return container();' in script
    for member in (
        "controller: { get() { return null; }",
        "ready: fixed(new Promise(() => {}))",
        "getRegistrations: fixed(() => Promise.resolve([]))",
        "getRegistration: fixed(() => Promise.resolve(undefined))",
        "startMessages: fixed(() => {})",
        "Object.freeze(Object.create(proto,",
    ):
        assert member in script, member
    assert "delete Navigator.prototype.serviceWorker" in script


def test_the_worker_gate_captures_what_it_reads_and_stringifies_once():
    script = actions.SIGNED_IN_JS
    gate = script[script.index("const RealString") : script.index("const blocked")]
    assert "RealString = String, construct = Reflect.construct" in gate
    assert "RealURL = URL, here = location.href" in gate
    assert 'getter("origin")' in gate
    assert 'getter("pathname")' in gate
    worker = script[script.index("function Worker(") : script.index("value.prototype")]
    assert worker.count("RealString(url)") == 1
    assert "construct(RealWorker, [text, options], new.target)" in worker
    assert "own(text)" in worker
    assert "Reflect.construct" not in worker
    assert "own(url)" not in worker


@pytest.mark.parametrize(
    "action",
    [
        "https://www.welcometothejungle.com/fr/newsletter/subscribe",
        "https://www.welcometothejungle.com/fr/jobs/alerts",
        "https://www.welcometothejungle.com/fr/jobs?save=1",
        "https://www.welcometothejungle.com/fr/companies/acme/follow",
    ],
)
def test_a_consent_form_never_posts_to_a_subscribe_save_or_follow_address(action):
    page = "https://www.welcometothejungle.com/fr/jobs"
    shape = banner_button("Close without accepting cookies", method="post", action=action)
    assert not discovery.consent_form(shape, page)
    fine = banner_button(
        "Close without accepting cookies", method="post", action=f"{page.rsplit('/', 1)[0]}/consent"
    )
    assert discovery.consent_form(fine, page)


def test_the_warning_reads_the_decline_with_the_page_s_address():
    page = "https://www.welcometothejungle.com/fr/jobs"
    shape = banner_button("Close without accepting cookies", method="post", action="")
    assert discovery.decline(shape, page)
    assert "decline=1" in actions.described(shape, page)
    assert "decline=0" in actions.described(shape)  # with no address, as the bug read it
