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
            [(JOB + "7002", "Robot Welder Invented Robotics")],
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
    ],
)
def test_the_cards_of_a_script_result(result, code: str, found: list) -> None:
    raw = json.dumps(result)
    assert finder._script_cards(SCRIPT_URL, raw, code) == found


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
    monkeypatch.setattr(finder, "APP_OPENS_MAX", 0)
    again, _ = listed((LIST, LIST_PAGE))
    answer = json.loads(asyncio.run(again.report_verified([item]))[0])
    assert answer["kept"] == 1
    assert again.refused["app_opens_max"] == 0
    assert batches[0].findings[0].list_url == finder.canonical(LIST)


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
