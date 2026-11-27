"""Jobs J31 (owner, 2026-11-26: "Oui, avec une mention"): a posting seen only as a card of a list,
its own page not read, is kept from the card with report_listing, and only when the session saw
that card: its link on a page read or fetched, or an item of a script's result, with the title's
words in that card's own text. A stopped session still takes the reports of what it read (the
production run of 2026-11-26 lost 65 postings to "Stopped: browser_memory"), and a posting the
app cannot open stands on its card. Invented hosts and titles only."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.browser import finder
from tests.browser.test_b13_runtime import BASE, InteractiveTools, session

LIST = BASE + "emplois?q=data&l=saint-denis"
JOB = BASE + "jobs/"
OTHER = "https://board2.example.test/jobs/101"
LIST_PAGE = "\n".join(
    [
        '- heading "Data jobs in Saint-Denis" [level=1]',
        "- list:",
        "  - listitem:",
        '    - link "Data Scientist H/F" [ref=e1]:',
        f"      - /url: {JOB}101",
        "    - text: Example Robotics, Saint-Denis",
        "  - listitem:",
        '    - heading "ML Engineer" [level=3]:',
        '      - link "Voir l\'offre" [ref=e2]:',
        "        - /url: /jobs/102",
        "  - listitem:",
        '    - link "GenAI Engineer Example Labs Saint-Pierre 2026-11-20" [ref=e3]:',
        "      - /url: /jobs/103",
        "  - listitem:",
        '    - link "Data Analyst" [ref=e4]:',
        "      - /url: /jobs/104",
        "  - listitem:",
        '    - link "Page 2" [ref=e5]:',
        f"      - /url: {OTHER}",
    ]
)
CHECK_PAGE = '- heading "Just a moment" [level=1]\n- paragraph: Checking your browser'


def listed(*pages: tuple[str, str]) -> tuple[finder.FinderSession, list]:
    batches: list = []

    async def commit(batch):
        batches.append(batch)

    one = session(InteractiveTools(), "e2e-person")
    one.on_verified = commit
    one.clock = lambda: datetime(2026, 11, 26, 9, tzinfo=UTC)
    for url, snapshot in pages:
        one._read(url, snapshot)
    return one, batches


def cards(one: finder.FinderSession, *items: dict) -> dict:
    answer, _ = asyncio.run(one.report_listing(list(items)))
    return json.loads(answer)


@pytest.mark.parametrize(
    ("title", "url"),
    [
        ("Data Scientist H/F", JOB + "101"),
        ("data scientist", JOB + "101"),  # case, the gender marker aside
        ("Data Scientist", "/jobs/101"),  # read against the address of the session
        ("ML Engineer", JOB + "102"),  # the heading that holds its link
        ("GenAI Engineer", JOB + "103"),  # a link that wraps the card
        ("Data  Analyst", JOB + "104"),  # spacing aside
    ],
)
def test_a_card_seen_on_a_page_read_is_kept_with_its_list(title: str, url: str) -> None:
    one, batches = listed((LIST, LIST_PAGE))
    assert cards(one, {"title": title, "url": url})["kept"] == 1
    [finding] = batches[0].findings
    assert finding.list_url == finder.canonical(LIST)
    assert finding.evidence["rule"] == "card"
    assert finding.evidence["card"] == "page"
    assert finding.text  # the card's own text, never the model's


@pytest.mark.parametrize(
    ("title", "url", "reason"),
    [
        (t, u, "card_not_seen")
        for t, u in [
            ("Data Analyst", JOB + "101"),
            ("Example Robotics", JOB + "101"),
            ("Senior Data Scientist", JOB + "101"),
            ("ML Engineer", JOB + "101"),
            ("Data Scientist", JOB + "101?ref=list"),
            ("Data Scientist", JOB + "101/"),
            ("Data Scientist", "http://jobs.example.test/jobs/101"),
            ("Data Scientist", "https://www.jobs.example.test/jobs/101"),
            ("Data Scientist", "https://board2.example.test/jobs/1010"),
            ("Data Scientist", JOB + "999"),
            ("Data Scientist", BASE + "job/101"),
        ]
    ]
    + [
        ("Data jobs", LIST, "not_a_posting_page"),  # the list itself
        ("Search jobs", BASE, "not_a_posting_page"),  # the start page
        ("", JOB + "101", "missing_field"),
        ("Data\x00Scientist", JOB + "101", "missing_field"),
        ("(H/F)", JOB + "101", "missing_field"),
        ("Data Scientist", "javascript:alert(1)", "not_an_address"),
    ],
)
def test_a_card_the_session_did_not_see_is_refused(title: str, url: str, reason: str) -> None:
    one, batches = listed((LIST, LIST_PAGE))
    answer = cards(one, {"title": title, "url": url})
    assert answer["kept"] == 0
    assert answer["refused"][0]["reason"] == reason
    assert answer["refused"][0].get("do") or reason not in finder.REPORT_ADVICE
    assert batches == []
    assert one.cards_refused[reason] == 1


def test_a_card_of_a_page_read_in_another_run_is_refused() -> None:
    listed((LIST, LIST_PAGE))
    other, batches = listed()
    answer = cards(other, {"title": "Data Scientist", "url": JOB + "101"})
    assert answer["refused"][0]["reason"] == "card_not_seen"
    assert batches == []


def test_a_page_read_that_shows_the_title_goes_to_report_postings() -> None:
    page = '- heading "Data Scientist" [level=1]\n- paragraph: Build invented models.'
    one, _ = listed((LIST, LIST_PAGE), (JOB + "101", page))
    assert cards(one, {"title": "Data Scientist", "url": JOB + "101"})["refused"][0] == {
        "index": 0,
        "reason": "page_readable",
        "do": finder.REPORT_ADVICE["page_readable"],
    }


def test_a_posting_page_behind_a_check_is_kept_from_its_card() -> None:
    one, batches = listed((LIST, LIST_PAGE), (JOB + "101", CHECK_PAGE))
    assert cards(one, {"title": "Data Scientist", "url": JOB + "101"})["kept"] == 1
    assert batches[0].findings[0].list_url == finder.canonical(LIST)


def test_the_cards_fields_are_kept_only_when_the_card_shows_them() -> None:
    one, batches = listed((LIST, LIST_PAGE))
    item = {"title": "GenAI Engineer", "url": JOB + "103", "company": "Example Labs"}
    cards(one, {**item, "location": "Saint-Pierre", "published_on": "2026-11-20"})
    cards(one, {"title": "Data Analyst", "url": JOB + "104", "company": "Invented Corp",
                "location": "Paris", "published_on": "2026-11-31"})  # fmt: skip
    first, second = (batch.findings[0] for batch in batches)
    assert (first.company, first.location, first.published_on) == (
        "Example Labs",
        "Saint-Pierre",
        "2026-11-20",
    )
    assert (second.company, second.location, second.published_on) == (None, "", None)


def test_a_fetched_list_gives_cards_and_a_web_search_result_alone_none() -> None:
    fetched = '- heading "Jobs" [level=1]\n- link "Data Engineer Example Corp":\n  - /url: ' + OTHER
    one, batches = listed()
    one._read("https://aggregator.example.test/list", fetched, kind="fetch")
    one.note_niche_links([("Data Scientist", JOB + "105")])  # a result: no card
    answer = cards(
        one,
        {"title": "Data Engineer", "url": OTHER},
        {"title": "Data Scientist", "url": JOB + "105"},
    )
    assert answer["kept"] == 1
    assert answer["refused"][0]["reason"] == "card_not_seen"
    assert batches[0].findings[0].evidence["card"] == "fetch"


SCRIPT_URL = BASE + "search?q=robot"


@pytest.mark.parametrize(
    ("result", "code", "found"),
    [
        (
            [{"title": "Robot Welder", "company": "Invented Robotics", "link": "/jobs/7002"}],
            "() => [...document.querySelectorAll('a')].map(a => ({title: a.textContent}))",
            [(JOB + "7002", "Robot Welder\nInvented Robotics")],  # a line per string
        ),
        (  # an address the script's own code writes is the model's word, never a card
            [{"title": "Robot Welder", "link": "/jobs/9999"}],
            "() => [{title: 'Robot Welder', link: '/jobs/9999'}]",
            [],
        ),
        (  # the items of a list are cards; an object around them holds none of their titles
            {"page": "Robot jobs", "jobs": [{"t": "Robot Arm", "u": "/jobs/1"}, {"t": "Welder"}]},
            "() => read()",
            [(JOB + "1", "Robot Arm")],
        ),
        (  # a text result: one card per line
            "Robot Arm | https://jobs.example.test/jobs/1\nWelder | /jobs/2",
            "() => text()",
            [(JOB + "1", "Robot Arm |")],  # a path is an address only alone or in JSON
        ),
        (
            {"title": "Robot Arm", "url": {"href": "/jobs/3"}},
            "() => one()",
            [(JOB + "3", "Robot Arm")],
        ),
        (  # objects of an object are cards of their own; the one around them gives none
            {"a": {"t": "Robot Arm", "u": "/jobs/1"}, "b": {"t": "Welder", "u": "/jobs/2"}},
            "() => byId()",
            [(JOB + "2", "Welder"), (JOB + "1", "Robot Arm")],
        ),
        (  # a text naming two addresses gives none: its words cannot be told apart
            "Robot Arm https://jobs.example.test/jobs/1 Welder https://jobs.example.test/jobs/2",
            "() => joined()",
            [],
        ),
        (  # an address built from a piece read on the page counts as seen
            [{"title": "Robot Welder", "link": "/jobs/7002"}],
            "() => cards.map(c => ({title: c.innerText, link: '/jobs/' + c.dataset.id}))",
            [(JOB + "7002", "Robot Welder")],
        ),
        (  # an address built from a piece the code writes is the model's word
            [{"title": "Robot Welder", "link": "/jobs/9999"}],
            "() => [{title: document.title, link: '/jobs/' + 9999}]",
            [],
        ),
        (  # a title the code writes is no word of the card
            [{"title": "Robot Painter", "link": "https://jobs.example.test/jobs/7003"}],
            "() => [{title: 'Robot Painter', link: document.links[3].href}]",
            [],
        ),
    ],
)
def test_the_cards_of_a_script_result(result, code: str, found: list) -> None:
    raw = json.dumps(result)
    assert finder._script_cards(SCRIPT_URL, raw, code) == found


def test_a_cut_script_result_gives_no_card_of_two_postings() -> None:
    """A result browserd cut is no JSON: read as text, its one line names every posting, so it
    gives none (before, any title of it stood for any of its addresses)."""
    whole = json.dumps([{"title": f"Robot Role {i}", "link": f"{JOB}{7000 + i}"} for i in range(9)])
    assert finder._script_cards(SCRIPT_URL, whole[: len(whole) // 2], "() => all()") == []
    one, batches = listed()
    asyncio.run(one._keep_script(SCRIPT_URL, whole[: len(whole) // 2], True, "() => all()"))
    assert cards(one, {"title": "Robot Role 0", "url": JOB + "7001"})["kept"] == 0
    assert batches == []


CATEGORY_PAGE = "\n".join(
    [
        '- heading "Data" [level=1]',
        '- link "Data Scientist jobs" [ref=e1]:',
        f"  - /url: {BASE}categories/data-scientist",
        '- link "Data Scientist in Lyon" [ref=e2]:',
        f"  - /url: {BASE}search?q=data+scientist&l=lyon",
        '- link "Emplois Data Scientist" [ref=e3]:',
        f"  - /url: {BASE}emplois-data-scientist",
        '- link "Data Scientist, Example Robotics" [ref=e4]:',
        f"  - /url: {BASE}jobs/201",
    ]
)


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        (BASE + "categories/data-scientist", False),  # a category, under fewer than 5 cards
        (BASE + "search?q=data+scientist&l=lyon", False),  # a search address
        (BASE + "emplois-data-scientist", False),  # a listing word first
        (BASE + "jobs/201", True),  # a posting's address on the same small page
    ],
)
def test_a_search_or_listing_address_is_no_card(url: str, kept: bool) -> None:
    one, _ = listed((BASE + "domaines/data", CATEGORY_PAGE))
    answer = cards(one, {"title": "Data Scientist", "url": url})
    assert answer["kept"] == int(kept)
    assert kept or answer["refused"][0]["reason"] == "not_a_posting_page"


@pytest.mark.parametrize(
    ("status", "kept"),
    [(status, True) for status in (401, 403, 429, 503)]
    + [(status, False) for status in (404, 410, 400, 500, 502, None)],
)
def test_a_card_stands_in_for_a_walled_page_never_a_gone_one(status, kept: bool) -> None:
    """Its page answered an error status: walled (a check, a sign in, a rate limit) the card
    stands in; gone, failed or unknown it is refused `page_error`, as report_postings is (B21)."""
    one, _ = listed((LIST, LIST_PAGE))
    one.error_pages[finder.canonical(JOB + "101")] = status
    answer = cards(one, {"title": "Data Scientist", "url": JOB + "101"})
    assert answer["kept"] == int(kept)
    assert kept or answer["refused"][0]["reason"] == "page_error"


def test_a_script_result_is_a_source_of_cards() -> None:
    one, batches = listed()
    raw = json.dumps([{"title": "Robot Welder", "link": "/jobs/7002"}])
    asyncio.run(one._keep_script(SCRIPT_URL, raw, False, "() => readCards()"))
    assert cards(one, {"title": "Robot Welder", "url": JOB + "7002"})["kept"] == 1
    assert batches[0].findings[0].evidence["card"] == "script"


@pytest.mark.parametrize("stop", sorted(finder.STORES_AFTER_STOP))
def test_a_stopped_session_still_keeps_what_it_read_and_opens_nothing(stop: str) -> None:
    """The production run of 2026-11-26: three result pages read, the tab closed three times for
    memory, then a report of 65 postings refused. Now a stop after which the store works keeps
    the pages read and the cards seen, and the app opens nothing."""
    page = '- heading "Data Analyst" [level=1]\n- paragraph: Read before the stop.'
    one, batches = listed((LIST, LIST_PAGE), (JOB + "104", page))
    one.stopped = stop
    opened: list[str] = []

    async def never(url: str) -> str:
        opened.append(url)
        return url

    one._open_for_report = never
    answer, error = asyncio.run(
        one.report_verified(
            [
                {"title": "Data Analyst", "url": JOB + "104", "text": "x"},  # its page was read
                {"title": "Data Scientist", "url": JOB + "101", "text": "x"},  # a card only
                {"title": "Robot", "url": JOB + "999", "text": "x"},  # never seen
            ]
        )
    )
    said = json.loads(answer)
    assert error
    assert said["kept"] == 2
    assert opened == []
    assert said["stopped"] == f"Stopped: {stop}. Report what you found, then stop."
    assert [r["reason"] for r in said["refused"]] == ["not_seen"]
    assert [f.list_url for b in batches for f in b.findings] == [None, finder.canonical(LIST)]


@pytest.mark.parametrize("stop", ["store_failed", "permission_revoked"])
def test_a_stop_that_ends_the_store_keeps_nothing_and_says_so(stop: str) -> None:
    one, batches = listed((LIST, LIST_PAGE))
    one.stopped = stop
    answer, error = asyncio.run(
        one.report_verified([{"title": "Data Scientist", "url": JOB + "101", "text": "x"}])
    )
    assert error
    assert answer == f"Stopped: {stop}. Nothing more is kept: stop now."
    assert (
        asyncio.run(one.report_listing([{"title": "Data Scientist", "url": JOB + "101"}]))[0]
        == answer
    )
    assert batches == []


def test_a_card_stands_in_when_the_app_cannot_open_the_address(monkeypatch) -> None:
    one, batches = listed((LIST, LIST_PAGE))

    async def failed(url: str) -> str:
        return ""  # the page did not load

    one._open_for_report = failed
    item = {"title": "Data Scientist", "url": JOB + "101", "text": "x"}
    assert json.loads(asyncio.run(one.report_verified([item]))[0])["kept"] == 1
    assert batches[0].findings[0].list_url == finder.canonical(LIST)
    # Past the opens of one call the page opens at the next one: no card stands in for it.
    monkeypatch.setattr(finder, "APP_OPENS_MAX", 0)
    again, stored = listed((LIST, LIST_PAGE))
    answer = json.loads(asyncio.run(again.report_verified([item]))[0])
    assert answer["kept"] == 0
    assert again.refused["app_opens_max"] == 1
    assert stored == []


def test_the_tool_and_the_prompts_say_when_to_use_it() -> None:
    from app.browser import finder_tools

    assert "report_listing" in finder.INTERACTIVE_TOOLS
    assert "report_listing" not in finder.ACTIONS
    schema = finder_tools.SCHEMAS["report_listing"]
    assert schema["properties"]["cards"]["maxItems"] == finder.POSTINGS_MAX == 100
    assert schema["properties"]["cards"]["items"]["required"] == ["title", "url"]
    for prompt in (finder.INTERACTIVE_PROMPT, finder.WEB_PROMPT):
        flat = " ".join(prompt.split())
        assert "report_listing" in flat
        assert "Never use report_listing for a" in flat
    assert "A search result alone is no card" in " ".join(finder.WEB_PROMPT.split())


def test_the_production_run_kept_its_cards_after_the_memory_stop() -> None:
    """Sweep 7f00a8a1 (2026-11-26), its shape replayed: a signed in search read three result
    pages, the browser closed its tab for memory three times (`browser_memory`), then the agent
    reported 65 postings it never opened, and all were refused. Now each card the session saw is
    kept, seen only in a list (the page it was on); the app opens nothing after the stop, and the
    answer says what the code does (STORES_AFTER_STOP)."""
    from tests.browser.test_b20_agent import HOME, START, run, selected, text_of, tool
    from tests.browser.test_b29_search_goes_on import GuardLoopSite

    lists = [START + f"search?q=data&page={page}" for page in (1, 2, 3)]
    pages = {START: HOME}
    for page, address in enumerate(lists):
        rows = ['- heading "Data jobs" [level=1]', "- list:"]
        for n in range(page * 22, page * 22 + 22):
            rows += [
                "  - listitem:",
                f'    - link "Data Role {n} H/F" [ref=e{n + 9}]:',
                f"      - /url: /jobs/{9100 + n}",
            ]
        pages[address] = "\n".join(rows)
    site = GuardLoopSite(pages=pages)
    postings = [
        {"title": f"Data Role {n} H/F", "url": f"/jobs/{9100 + n}", "text": "A card."}
        for n in range(65)
    ] + [{"title": "Data Role 7", "url": "/jobs/9999", "text": "Made up."}]
    seen: dict = {"kept": []}

    async def script(session: finder.FinderSession):
        async def keep(batch):
            seen["kept"].extend(batch.findings)

        session.on_verified = keep  # the store of the sweep (commit_verified), as it is called
        for address in lists:
            await tool(session, "browser_navigate", url=address)
        for _ in range(finder.MEMORY_CLOSES_MAX):
            await tool(session, "browser_click", ref="e9")
        calls = len(site.calls)
        seen["report"] = json.loads(
            text_of(await tool(session, "report_postings", postings=postings))
        )
        seen["after"] = site.calls[calls:]
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert entry["error"] == "browser_memory"
    assert seen["report"]["kept"] == 65
    assert [r["reason"] for r in seen["report"]["refused"]] == ["not_seen"]
    assert seen["report"]["stopped"] == "Stopped: browser_memory. Report what you found, then stop."
    assert "browser_memory" in finder.STORES_AFTER_STOP
    assert seen["after"] == []  # nothing opened, by the app or anyone, after the stop
    assert [f.list_url for f in seen["kept"]] == [lists[n // 22] for n in range(65)]
    assert all(f.evidence["rule"] == "card" for f in seen["kept"])


TITLES_PAGE = "\n".join(
    [
        '- heading "Data jobs" [level=1]',
        "- list:",
        "  - listitem:",
        '    - link "Senior Data Engineer (Stage)" [ref=e1]:',
        f"      - /url: {JOB}301",
        "  - listitem:",
        '    - link "Senior Data Engineer (Stage) Example Robotics Paris" [ref=e2]:',
        f"      - /url: {JOB}302",
        '      - heading "Senior Data Engineer (Stage)" [level=3]',
        "      - text: Example Robotics Paris",
        "  - listitem:",
        '    - heading "Stage: Data Engineer" [level=3]:',
        '      - link "Voir l\'offre" [ref=e3]:',
        f"        - /url: {JOB}303",
        "  - listitem:",
        f'    - link "Data Engineer {"and more words " * 22}" [ref=e4]:',
        f"      - /url: {JOB}304",
        "  - listitem:",
        '    - link "Data Scientist H/F" [ref=e5]:',
        f"      - /url: {JOB}305",
    ]
)


@pytest.mark.parametrize(
    ("url", "reported", "stored"),
    [
        (JOB + "301", "Data Engineer", "Senior Data Engineer (Stage)"),
        (JOB + "301", "data engineer stage", "Senior Data Engineer (Stage)"),
        (JOB + "301", "Senior Data Engineer", "Senior Data Engineer (Stage)"),
        (JOB + "301", "Senior Data Engineer (Stage)", "Senior Data Engineer (Stage)"),
        (JOB + "301", "Engineer", "Senior Data Engineer (Stage)"),
        (JOB + "302", "Data Engineer", "Senior Data Engineer (Stage)"),  # its own heading
        (JOB + "302", "Robotics Paris", "Example Robotics Paris"),  # a label of the card
        (JOB + "303", "Data Engineer", "Stage: Data Engineer"),  # the heading that holds it
        (JOB + "303", "Data Engineer Voir l'offre", "Stage: Data Engineer Voir l'offre"),
        (JOB + "304", "Data Engineer", None),  # its one line is longer than a title
        (JOB + "305", "Data Scientist", "Data Scientist H/F"),
        (JOB + "305", "data scientist (h/f)", "Data Scientist H/F"),
    ],
)
def test_a_card_is_stored_with_its_own_words_never_shorter(url, reported, stored) -> None:
    """Review of J31a: the title stored is the card's own words that hold the reported ones, as
    B21 stores a page's heading, so the model's shortening never dodges the person's rules."""
    one, batches = listed((LIST, TITLES_PAGE))
    answer = cards(one, {"title": reported, "url": url})
    if stored is None:
        assert answer["refused"][0]["reason"] == "card_not_seen"
        return
    [finding] = batches[0].findings
    assert (finding.title, finding.evidence["title"]) == (stored, stored)


@pytest.mark.parametrize(
    ("item", "reported", "stored"),
    [
        (
            {"title": "Senior Data Engineer (Stage)", "company": "Example Robotics"},
            "Data Engineer",
            "Senior Data Engineer (Stage)",
        ),
        (
            {"level": "Senior", "title": "Data Engineer"},
            "Senior Data Engineer",
            "Senior Data Engineer",
        ),
        ({"title": "Data Engineer", "tag": "Stage"}, "Data Engineer Stage", "Data Engineer Stage"),
        ({"title": "Stage Data Engineer"}, "data engineer", "Stage Data Engineer"),
    ],
)
def test_a_script_card_is_stored_with_the_string_that_holds_the_title(item, reported, stored):
    one, batches = listed()
    raw = json.dumps([{**item, "link": "/jobs/7002"}])
    asyncio.run(one._keep_script(SCRIPT_URL, raw, False, "() => readCards()"))
    assert cards(one, {"title": reported, "url": JOB + "7002"})["kept"] == 1
    assert batches[0].findings[0].title == stored


def test_a_shortened_card_title_still_meets_the_persons_exclusions_and_rules() -> None:
    """The card "Senior Data Engineer (Stage)" reported "Data Engineer", by report_listing and by
    report_postings falling back on the card: the card's words are stored, so the unwanted title
    "stage" leaves it out, and a never apply rule on "senior" skips it in the store."""
    from app.browser.sources import interactive
    from app.jobs import never_apply
    from tests.browser.test_b21_vie import Commits, saved

    one, batches = listed((LIST, TITLES_PAGE))
    cards(one, {"title": "Data Engineer", "url": JOB + "301"})

    async def failed(url: str) -> str:
        return ""  # the app could not open it: its card stands in

    fallback, stored = listed((LIST, TITLES_PAGE))
    fallback._open_for_report = failed
    item = {"title": "Data Engineer", "url": JOB + "302", "text": "x"}
    assert json.loads(asyncio.run(fallback.report_verified([item]))[0])["kept"] == 1
    rule = never_apply.NeverApplyRule.parse("No senior roles: senior")
    for finding in (batches[0].findings[0], stored[0].findings[0]):
        assert finding.title == "Senior Data Engineer (Stage)"
        planned = interactive.searches_for(saved(BASE, title_exclude={"terms": ["stage"]}), BASE)
        site = interactive._Site(planned, commits=Commits(), source_id="s", host="example")
        asyncio.run(site.verified(finder.VerifiedBatch((finding,))))
        assert (site.commits.stored, site.unwanted) == ([], {finding.url})
        assert never_apply.matching_rule([rule], finding.company or "", finding.title) is rule


def fetching(one: finder.FinderSession, answer: dict) -> None:
    """browserd's web_fetch answers `answer`, whatever the address."""

    async def call(name, args):
        return answer

    one.pages.tools.call = call


@pytest.mark.parametrize("status", [404, 410, 500, 403, 503])
@pytest.mark.parametrize("landed", [JOB + "101", BASE + "not-found"])
def test_a_posting_that_answered_an_error_to_a_fetch_is_no_card(status, landed) -> None:
    """Review of J31a: web_fetch records the error status under the address asked for and the one
    it landed on, so a gone posting is refused `page_error`; a walled one stands on its card."""
    one, _ = listed((LIST, LIST_PAGE))
    fetching(one, {"ok": True, "url": landed, "snapshot": '- heading "Gone"', "status": status})
    asyncio.run(one.web_fetch(JOB + "101"))
    answer = cards(one, {"title": "Data Scientist", "url": JOB + "101"})
    walled = status in finder.WALL_STATUSES
    assert answer["kept"] == int(walled)
    assert walled or answer["refused"][0]["reason"] == "page_error"
    # A later success clears it.
    fetching(one, {"ok": True, "url": JOB + "101", "snapshot": CHECK_PAGE, "status": 200})
    asyncio.run(one.web_fetch(JOB + "101"))
    assert finder.canonical(JOB + "101") not in one.error_pages


@pytest.mark.parametrize("status", [404, 410, 500, 403, 503])
def test_a_posting_redirected_to_an_error_page_is_no_card(status) -> None:
    """The agent's own navigate to the posting landed on another address with an error status:
    both addresses are that posting's error, by report_listing and by report_postings."""
    from tests.browser.test_b20_agent import START, run, selected, text_of, tool
    from tests.browser.test_b27_code_first import BOARD, CARDS, SEEN_1, board, opening, scripted

    gone = START + "errors/not-found"
    site = board(evaluated=scripted(CARDS))
    site.pages[gone] = '- heading "Page not found" [level=1]'
    seen: dict = {}

    def answer(name, args):
        if name == "browser_navigate" and args["url"] == SEEN_1:
            site.current = gone
            return {"ok": True, "url": gone, "status": status}
        return None

    async def script(session: finder.FinderSession):
        stored: list = []

        async def keep(batch):
            stored.extend(batch.findings)

        session.on_verified = keep
        await tool(session, "browser_navigate", url=BOARD)
        await tool(session, "browser_evaluate", function="() => 1")
        opening(site, answer)
        await tool(session, "browser_navigate", url=SEEN_1)
        item = {"title": "Robot Arm Engineer", "url": SEEN_1}
        seen["listing"] = json.loads(text_of(await tool(session, "report_listing", cards=[item])))
        seen["stored"] = list(stored)
        return finder.Ran()

    run(selected(), site, script)
    walled = status in finder.WALL_STATUSES
    assert seen["listing"]["kept"] == int(walled)
    assert walled or seen["listing"]["refused"][0]["reason"] == "page_error"
    assert [f.list_url for f in seen["stored"]] == ([BOARD] if walled else [])


@pytest.mark.parametrize("status", [404, 403])
def test_report_postings_of_a_redirected_error_page_falls_back_on_a_walled_card_only(status):
    one, _ = listed((LIST, LIST_PAGE))
    one.error_pages[finder.canonical(JOB + "101")] = status

    async def never(url: str) -> str:
        raise AssertionError("an error page is never opened again")

    one._open_for_report = never
    item = {"title": "Data Scientist", "url": JOB + "101", "text": "x"}
    answer = json.loads(asyncio.run(one.report_verified([item]))[0])
    assert answer["kept"] == int(status == 403)
    assert status == 403 or answer["refused"][0]["reason"] == "page_error"


@pytest.mark.parametrize("action", ["close", "select"])
@pytest.mark.parametrize("code", ["permission_revoked", "permission_unavailable"])
def test_a_permission_taken_back_while_the_app_gives_its_tab_back_keeps_nothing(action, code):
    """Review of J31a: the permission goes between the app's open and the give back of its tab:
    the website stops with the permission's own code, never `guard_failed`, so neither the page
    the app read nor any card is kept."""
    from app.browser.sources import SourceError
    from tests.browser.test_b27_code_first import CARDS, board, report_two, scripted

    site = board(evaluated=scripted(CARDS))

    def answer(name, args):
        if name == "browser_tabs" and args["action"] == action:
            return SourceError(code)
        return None

    entry, seen, urls = report_two(site, answer)
    assert seen["stopped"] == code
    assert seen["answer"] == f"Stopped: {code}. Nothing more is kept: stop now."
    assert urls == []
    assert seen["opened_by_app"] == 1  # the app read the first page before the permission went
    assert entry["error"] == code


class Permits:
    """GuardedTools' permission check alone, answering `code` (None: still given)."""

    def __init__(self, tools, code: str | None) -> None:
        self.tools, self.code, self.checked = tools, code, 0

    async def call(self, name, args):
        raise AssertionError("a stopped session makes no browser call")

    async def permit(self) -> None:
        from app.browser.sources import SourceError

        self.checked += 1
        if self.code is not None:
            raise SourceError(self.code)


@pytest.mark.parametrize("code", [None, "permission_revoked", "permission_checkpoint"])
def test_a_permission_taken_back_after_a_memory_stop_keeps_nothing(code) -> None:
    """Review of J31a: a session stopped for memory makes no browser call, so its report checks
    the permission first; taken back, nothing is kept and the answer says so."""
    one, batches = listed((LIST, LIST_PAGE))
    one.stopped = "browser_memory"
    one.pages.tools = permits = Permits(one.pages.tools, code)
    item = {"title": "Data Scientist", "url": JOB + "101"}
    answer, error = asyncio.run(one.report_listing([item]))
    assert error
    assert permits.checked == 1
    if code is None:
        assert json.loads(answer)["kept"] == 1
        return
    assert answer == f"Stopped: {code}. Nothing more is kept: stop now."
    assert (batches, one.stopped) == ([], code)


def test_guarded_tools_check_the_permission_alone() -> None:
    from app.browser.sources import SourceError, interactive

    class Policy:
        async def check(self, context):
            return "permission_revoked"

    context = interactive.SourceContext("e2e-person", "sweep", "s", "custom", BASE)
    guarded = interactive.GuardedTools(object(), Policy(), context)
    with pytest.raises(SourceError) as raised:
        asyncio.run(guarded.permit())
    assert raised.value.code == "permission_revoked"
