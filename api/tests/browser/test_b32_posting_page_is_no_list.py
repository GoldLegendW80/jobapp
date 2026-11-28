"""Browser B32 (#865): a posting's page with a header and a footer is not a list. A real posting
page shows LIST_CARDS_MIN links with words in its header, navigation and footer alone, so J31's
rule counted it as a list, and the session read it as none of the posting's: a report under the
list's spelling opened it again, and kept it from its card when that open failed. Every page
here is in `lists`, so what decides is the posting's own address, never the count. Invented
hosts and titles only."""

from typing import Any

import pytest

from app.browser import finder
from tests.browser.posting_pages import FOOTER, HEADER, posting
from tests.browser.test_b31_same_posting import (
    INDEED,
    LINKEDIN,
    board_session,
    listed,
    reported,
)
from tests.browser.test_j36_listed_pages import BODY, HEADED_LIST, LAYOUTS, short_posting

START = "https://jobs.example.test/"
JK = "5f1c2d3e4a5b6c7d"
LI = "4012345678"
TITLE = "Robot Welder"


def list_of(heading: str, links: list[tuple[str, str]]) -> str:
    """A page of results: its heading and one card a link, each with a line of text."""
    rows = [f'- heading "{heading}" [level=1]', "- list:"]
    for n, (title, url) in enumerate(links):
        rows += [
            "  - listitem:",
            f'    - heading "{title}" [level=3]',
            f'    - link "{title}" [ref=c{n}]:',
            f"      - /url: {url}",
            "    - paragraph: Example Robotics, Paris",
        ]
    return "\n".join(rows)


def others(count: int) -> list[tuple[str, str]]:
    return [(f"Robot Role {chr(65 + n)}", f"/jobs/{500 + n}") for n in range(count)]


# Through a report (acceptance 3): the list shows the posting under one spelling, the agent read
# its page under another and reports the list's. (start, the list's address, the list's link,
# the address the agent read.)
SPELLINGS = {
    "indeed": (
        INDEED,
        INDEED + "jobs?q=robot&l=bern",
        f"/rc/clk?jk={JK}&bb=x0&xkcb=y&vjs=3&tk=t1&from=serp",
        f"{INDEED}viewjob?jk={JK}&from=serp&vjs=3",
    ),
    "linkedin": (
        LINKEDIN,
        LINKEDIN + "jobs/search/?keywords=robot",
        f"/jobs/view/robot-welder-at-example-robotics-{LI}?refId=a1&trackingId=b2&trk=public_jobs",
        f"{LINKEDIN}jobs/view/{LI}/",
    ),
    "careers page": (
        START,
        START + "search?q=robot",
        "/offres/246286?utm_source=list&utm_medium=web&gclid=x1",
        START + "offres/246286",
    ),
}


@pytest.mark.parametrize("site", list(SPELLINGS))
def test_a_page_read_under_one_spelling_is_kept_under_the_lists_one(site: str) -> None:
    """No second open (the app's open fails here, so an open would keep it from its card), a
    full posting stored from the page read, no mention."""
    start, list_url, link, read = SPELLINGS[site]
    page = posting(TITLE, BODY)
    one, batches, opened, _ = board_session(
        start, (list_url, list_of("Robot jobs", [(TITLE, link), *others(5)])), (read, page)
    )
    assert read in one.lists  # J31's count reads the posting's page as a list
    answer = reported(one, {"title": TITLE, "url": link, "text": "A card."})
    assert (answer["kept"], answer["refused"], opened) == (1, [], [])
    assert "from_card" not in answer
    [finding] = batches[0].findings
    assert (finding.url, finding.title, finding.list_url) == (read, TITLE, None)
    assert finding.evidence["rule"] == "heading"
    assert "welding cells of an invented factory" in finding.text  # the page's, never the card's


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_the_layouts_of_real_posting_pages_are_no_list(name: str) -> None:
    """J36's posting pages as websites lay them out: each, read at its own address, is the
    posting's page for report_listing (`page_readable`) and for a report under another
    spelling (kept, no open)."""
    read = START + "jobs/1"
    one, batches, opened, _ = board_session(START, (read, LAYOUTS[name]))
    assert read in one.lists
    assert listed(one, {"title": TITLE, "url": read})["refused"][0]["reason"] == "page_readable"
    answer = reported(one, {"title": TITLE, "url": read + "?utm_source=x", "text": "A card."})
    assert (answer["kept"], opened, batches[0].findings[0].url) == (1, [], read)


WALL = "\n".join(
    [HEADER, "- main:", '  - heading "Sign in to see this job" [level=1]',
     '  - textbox "Email" [ref=w1]', FOOTER]
)  # fmt: skip
SEARCH = START + "search?q=robot&l=paris"
RESULTS_AT = START + "s/robot-paris"
COMPANY = START + "c/example-robotics"
POSTING = START + "jobs/1"
UNLISTED = START + "jobs/77"
WALLED = START + "jobs/2"

# Which pages stay lists (the pull request's table): (row, its address, its page, what the list
# showed, report_listing's answer for TITLE at that address, whether `_own_page` takes it). The
# list read first shows every address below but UNLISTED as a card titled TITLE.
TABLE = [
    ("a search page", SEARCH, list_of("Robot jobs near Paris", [(TITLE, "/jobs/1"), *others(19)]),
     "not_a_posting_page", False),
    ("a page of results with 20 cards", RESULTS_AT,
     list_of("Robot jobs near Paris", [(TITLE, "/jobs/1"), *others(19)]),
     "not_a_posting_page", True),
    ("a company's page of 6 postings", COMPANY,
     list_of("Example Robotics", [(TITLE, "/jobs/1"), *others(5)]), "not_a_posting_page", True),
    ("a page of results headed with the title", RESULTS_AT, HEADED_LIST, "page_readable", True),
    ("a posting page with 8 similar postings", POSTING, short_posting("region"),
     "page_readable", True),
    ("a posting page whose address the list never showed", UNLISTED, posting(TITLE, BODY),
     "page_readable", True),
    ("a wall at the posting's address, with a header and a footer", WALLED, WALL,
     "not_a_posting_page", True),
]  # fmt: skip


@pytest.mark.parametrize(
    ("row", "url", "page", "reason", "own"), TABLE, ids=[row[0] for row in TABLE]
)
def test_which_pages_stay_lists(row: str, url: str, page: str, reason: str, own: bool) -> None:
    shown = [(TITLE, a) for a in (SEARCH, RESULTS_AT, COMPANY, POSTING, WALLED)]
    one, batches, _, _ = board_session(
        START, (START + "browse", list_of("Robot jobs", [*shown, *others(3)])), (url, page)
    )
    assert url in one.lists, row  # J31's count alone would refuse each
    assert one._own_page(url) is own
    answer = listed(one, {"title": TITLE, "url": url})
    assert answer["kept"] == 0
    assert answer["refused"][0]["reason"] == reason
    assert batches == []


def test_a_list_kept_from_a_card_under_another_spelling_stays_a_list() -> None:
    """A list read at one address and reported under a spelling with trackers (one page by the
    rule of the same posting) is read as that list, never as an address no list was seen at."""
    one, batches, _, _ = board_session(
        START,
        (START + "browse", list_of("Robot jobs", [(TITLE, RESULTS_AT + "?utm_source=x")])),
        (RESULTS_AT, list_of("Robot jobs near Paris", others(20))),
    )
    answer = listed(one, {"title": TITLE, "url": RESULTS_AT + "?utm_source=x"})
    assert (answer["kept"], answer["refused"][0]["reason"], batches) == (
        0,
        "not_a_posting_page",
        [],
    )


def board_list(start: str, card: str) -> tuple[str, str]:
    return start + "browse", list_of("Robot jobs", [(TITLE, card), *others(5)])


OWN_BOARD_PAGES = [
    # The board's own page of a posting, whatever links it holds: read with its title, it is
    # the posting's page; read as a wall, its card stands in (J31), never a refusal as a list.
    (LINKEDIN, f"{LINKEDIN}jobs/view/{LI}/", f"{LINKEDIN}jobs/view/{LI}/", WALL, 1),
    (INDEED, f"{INDEED}viewjob?jk={JK}", f"{INDEED}viewjob?jk={JK}&from=serp", WALL, 1),
    (LINKEDIN, f"{LINKEDIN}jobs/view/{LI}/", f"{LINKEDIN}jobs/view/{LI}/", posting(TITLE, BODY), 0),
    (INDEED, f"{INDEED}viewjob?jk={JK}", f"{INDEED}m/viewjob?jk={JK}", posting(TITLE, BODY), 0),
]


@pytest.mark.parametrize(("start", "card", "url", "page", "kept"), OWN_BOARD_PAGES)
def test_the_boards_own_page_of_a_posting_is_never_a_list(
    start: str, card: str, url: str, page: str, kept: int
) -> None:
    one, batches, opened, _ = board_session(start, board_list(start, card), (url, page))
    assert url in one.lists
    assert one._own_page(url)
    answer = listed(one, {"title": TITLE, "url": card})
    assert answer["kept"] == kept
    if kept:
        assert batches[0].findings[0].list_url == finder.canonical(start + "browse")
    else:
        assert answer["refused"][0]["reason"] == "page_readable"
    assert opened == []


@pytest.mark.parametrize(
    ("start", "card", "url"),
    [
        (LINKEDIN, f"{LINKEDIN}jobs/view/{LI}/",
         f"{LINKEDIN}jobs/search/?currentJobId={LI}&keywords=robot"),
        (INDEED, f"{INDEED}viewjob?jk={JK}", f"{INDEED}cmp/Example-Robotics/jobs?jk={JK}"),
    ],
)  # fmt: skip
def test_a_boards_page_holding_the_id_is_never_the_postings(
    start: str, card: str, url: str
) -> None:
    """A search or a company page that holds the posting's id and shows its title is not the
    posting's page: the app opens the board's own address of the posting."""
    one, _, opened, _ = board_session(start, board_list(start, card), (url, posting(TITLE, BODY)))
    assert not one._own_page(url)
    reported(one, {"title": TITLE, "url": card, "text": "A card."})
    assert opened == [card]


def _items(answer: dict[str, Any]) -> list[str]:
    return [r["reason"] for r in answer["refused"]]


def test_a_made_up_address_stays_refused_beside_a_page_read() -> None:
    """B21's guard: a page read at the posting's own address names no other posting."""
    one, batches, opened, _ = board_session(START, (POSTING, posting(TITLE, BODY)))
    answer = reported(
        one,
        {"title": TITLE, "url": START + "jobs/10", "text": "A card."},
        {"title": TITLE, "url": POSTING + "?ref=made-up", "text": "A card."},
    )
    assert (answer["kept"], _items(answer), opened, batches) == (
        0,
        ["not_seen", "not_seen"],
        [],
        [],
    )
    assert listed(one, {"title": TITLE, "url": START + "jobs/10"})["refused"][0]["reason"] == (
        "card_not_seen"
    )
