"""Jobs J36 (owner, 2026-11-27: "Oui", LinkedIn signed in included): after a website's agent,
the app reads by itself the pages of the postings kept from a card, one at a time in a tab of
its own (B27's open), and stores what each page answers. The store is a fake here (the jobs
tests run the real one): browserd's tabs are test_b27's `TabSite`, the permission a policy of
the website's GuardedTools. Invented hosts only, never a real job website."""

import asyncio
import math
import random
import re
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder, sources
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.sources import interactive, listed_pages
from app.jobs import sweep
from tests.browser import posting_pages
from tests.browser.posting_pages import FOOTER, HEADER
from tests.browser.test_b20_agent import HOME, START
from tests.browser.test_b27_code_first import TabSite

HOST = "jobs.example.test"
TODAY = datetime(2026, 11, 27, 23, 30, tzinfo=UTC)  # 28 November in Europe/Paris


def job(n: int) -> str:
    return f"{START}jobs/{n}"


# A posting's text as long as a posting's is (service.TEXT_MIN_CHARS, 300), invented.
BODY = (
    "- paragraph: Build invented robot arms with Python for Example Robotics, Paris. You design "
    "the welding cells of an invented factory, write the control software that drives them, test "
    "it on the bench with the team, and ship it to the line. You know Python, a robot framework "
    "and the safety rules of a workshop. Hybrid, two days a week on site, full time."
)


def posting(title: str, body: str = BODY) -> str:
    return posting_pages.posting(title, body)


def bare(title: str) -> str:
    """A posting's page with no link at all (the fakes of round 1)."""
    return f'- heading "{title}" [level=1]\n{BODY}'


SIGNIN = '- heading "Sign in" [level=1]\n- textbox "Email" [ref=e1]'
CHECK = '- heading "Just a moment" [level=1]\n- paragraph: Checking your browser'
CLOSED = '- heading "Robot Welder" [level=1]\n- paragraph: This job is no longer available.'
HINTED = f'- heading "Robot Welder" [level=1]\n- paragraph: Applications closed\n{BODY}'
# A page of the website that is not the posting's, with as much text as one.
ELSEWHERE = f'- heading "Example jobs near Paris" [level=1]\n- paragraph: 12 jobs found\n{BODY}'
SHORT = '- heading "Robot Welder" [level=1]\n- paragraph: Build invented robot arms in Paris.'
# A wall whose own heading holds the posting's title (review of PR 864, item 4).
TITLED_SIGNIN = f'- heading "Robot Welder" [level=1]\n{SIGNIN.replace("level=1", "level=2")}'
TITLED_CHECK = f'- heading "Robot Welder" [level=2]\n{CHECK}\n{BODY}'
GUEST = f'- heading "Robot Welder" [level=1]\n- heading "Join LinkedIn" [level=2]\n{BODY}'
TITLES = ("Robot Welder", "Robot Painter", "Robot Arm Engineer", "Robot Tester", "Robot Pilot",
          "Robot Fitter")  # fmt: skip
HINTED_BODY = f"- paragraph: Applications closed\n{BODY}"
SHORT_BODY = "- paragraph: Build invented robot arms in Paris."
# The website's search page listing the posting's title among six cards (review, item 3).
LIST_PAGE = "\n".join(
    ['- heading "Robot jobs near Paris" [level=1]', "- list:"]
    + [
        f'  - listitem:\n    - heading "{t}" [level=3]\n    - link "{t}" [ref=e{i}]:\n'
        f"      - /url: /jobs/{40 + i}"
        for i, t in enumerate(TITLES)
    ]
)


def cards(count: int, role: str = "listitem", snippet: str = "Example Robotics, Paris") -> str:
    """`count` cards of other postings, each a block of `role` with a link and a line of text."""
    return "\n".join(
        f'- {role}:\n  - link "Robot Job {chr(65 + i)}" [ref=c{i}]:\n    - /url: /jobs/{70 + i}\n'
        f"  - paragraph: {snippet}"
        for i in range(count)
    )


# A list whose heading holds the posting's title (a search for it), served at its own address.
HEADED_LIST = (
    '- heading "Robot Welder jobs in Paris" [level=1]\n- paragraph: 6 jobs found\n'
    + cards(
        6, snippet="Build invented robot arms with Python for Example Robotics, two days on site."
    )
)


def indent(text: str, by: int) -> str:
    return "\n".join(" " * by + line for line in text.split("\n"))


# Posting pages as websites lay them out (review of PR 864, round 3), each with the header and
# footer of `posting()`: every one is the posting's own page and must read. Invented.
COMPANY = '- link "Example Robotics" [ref=t1]:\n  - /url: /companies/example-robotics'
# The posting wrapped in an article that holds its company's link.
ARTICLE = "\n".join(
    [HEADER, "- main:", "  - article:", '    - heading "Robot Welder" [level=1]',
     indent(COMPANY, 4), "    - paragraph: Paris, France", indent(BODY, 4), FOOTER]
)  # fmt: skip
# Sibling sections: a top card, the description, about the company, apply.
SECTIONS = "\n".join(
    [HEADER, "- main:",
     '  - region "Robot Welder":', '    - heading "Robot Welder" [level=1]', indent(COMPANY, 4),
     "    - paragraph: Paris, France",
     '  - region "Description":', '    - heading "Description" [level=2]', indent(BODY, 4),
     '  - region "About the company":', '    - heading "About Example Robotics" [level=2]',
     "    - paragraph: Example Robotics builds invented robot arms for invented factories.",
     '    - link "Visit the website" [ref=t2]:', "      - /url: https://example-robotics.test/",
     '  - region "Apply":', '    - heading "Apply" [level=2]',
     '    - link "Apply on the employer site" [ref=t3]:',
     "      - /url: https://ats.example-hire.test/jobs/1",
     FOOTER]
)  # fmt: skip
ABOUT = (
    "- paragraph: Example Robotics is an invented maker of robot arms for invented factories. "
    "As a Robot Welder you design the welding cells of a new line, write the control software "
    "that drives them in Python, test it on the bench with the team and ship it to the line. "
    "You plan the cells with the process engineers, read the drawings of the parts, set the "
    "paths of the torches, tune the speeds and the currents, and check every seam with the "
    "quality team before a batch leaves the cell. You keep the safety rules of the workshop, "
    "train the operators on each new cell and write down what you changed. We ask for three "
    "years of welding automation, Python, a robot framework and good written English. The "
    "role is full time, hybrid with two days a week on site in Paris, with a yearly bonus, "
    "a meal card and five weeks of paid leave. Interviews are held in two rounds."
)


# LinkedIn like: a top card (the company's link, the title), an "About the job" article holding
# one link, the hiring team with a profile's link.
def linkedin(title: str) -> str:
    return LINKEDIN.replace('- heading "Robot Welder" [level=1]', f'- heading "{title}" [level=1]')


LINKEDIN = "\n".join(
    [HEADER, "- main:",
     "  - generic:", indent(COMPANY, 4), '    - heading "Robot Welder" [level=1]',
     "    - paragraph: Paris, France, 2 weeks ago, 40 applicants",
     '    - button "Easy Apply" [ref=l0]', '    - button "Save" [ref=l9]',
     "  - article:", '    - heading "About the job" [level=2]', indent(ABOUT, 4),
     '    - link "example-robotics.test/careers" [ref=l2]:',
     "      - /url: https://example-robotics.test/careers",
     '  - region "Meet the hiring team":', '    - heading "Meet the hiring team" [level=2]',
     '    - link "Alex Example" [ref=l3]:', "      - /url: /in/alex-example/",
     "    - paragraph: Hiring manager at Example Robotics",
     FOOTER]
)  # fmt: skip
# No `main`: the posting in nested blocks.
NO_MAIN = "\n".join(
    [HEADER, "- generic:", "  - generic:", "    - generic:",
     '      - heading "Robot Welder" [level=1]', "      - generic:", indent(BODY, 8), FOOTER]
)  # fmt: skip
# The posting's text as a bulleted list.
BULLETS = "\n".join(
    [HEADER, "- main:", '  - heading "Robot Welder" [level=1]', "  - paragraph: What you will do",
     "  - list:"]
    + [f"    - listitem: {item}" for item in (
        "Design the welding cells of an invented factory line",
        "Write the control software of the cells in Python",
        "Test it on the bench with the team every week",
        "Ship it to the line and train the operators",
        "Keep the safety rules of the workshop at all times",
        "Three years of welding automation and a robot framework",
    )]
    + [FOOTER]
)  # fmt: skip
SHORT_LINES = (
    "- paragraph: Example Robotics, Paris, France. Full time, hybrid, two days a week on site.",
    "- paragraph: You weld the frames of invented robot arms on a cell you set up and tune.",
    "- paragraph: You read the drawings, check every seam and keep the safety rules of the shop.",
    "- paragraph: Three years of welding, a robot framework and Python. Start in January.",
)
SIMILAR_EIGHT = "\n".join(
    ['  - region "Similar jobs":', '    - heading "Similar jobs" [level=2]', "    - list:"]
    + [
        f'      - listitem:\n        - link "Robot Job {chr(65 + i)}" [ref=s{i}]:\n'
        f"          - /url: /jobs/{60 + i}\n        - paragraph: Example Robotics, Lyon"
        for i in range(8)
    ]
)


def short_posting(banner: str) -> str:
    """A short posting (a title and four lines) with 8 similar postings and a cookie banner as
    a `region` or a `dialog`."""
    cookies = "\n".join(
        [f'- {banner} "Cookies":', "  - paragraph: This invented site uses cookies to run.",
         '  - button "Accept all" [ref=k1]', '  - button "Refuse all" [ref=k2]']
    )  # fmt: skip
    return "\n".join(
        [cookies, HEADER, "- main:", '  - heading "Robot Welder" [level=1]',
         indent("\n".join(SHORT_LINES), 2), SIMILAR_EIGHT, FOOTER]
    )  # fmt: skip


LAYOUTS = {
    "posting": posting("Robot Welder"),
    "article": ARTICLE,
    "sections": SECTIONS,
    "linkedin": LINKEDIN,
    "no_main": NO_MAIN,
    "bullets": BULLETS,
    "short_region": short_posting("region"),
    "short_dialog": short_posting("dialog"),
}
HOME_PAGE = '- heading "Example jobs" [level=1]\n- paragraph: Find your next invented job.'


class NoTools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"no browser call expected: {name}")


def page_of(url: str, snapshot: str) -> finder.PageRead:
    pages = sources.Pages(
        NoTools(), 1000, sleep=lambda _: asyncio.sleep(0), rng=random.Random(3),  # noqa: S311  deterministic test pacing
        monotonic=lambda: 0,
    )  # fmt: skip
    scope = finder.InteractiveScope(START, ("robot",), (), ())
    one = finder.FinderSession(pages, START, HOME, "e2e-person", interactive=scope)
    one._read(url, snapshot)
    return one.pages_read[url]


A = job(1)
# The accept and refuse table of what a page answers: (status, landed, snapshot, title,
# platform, verdict). The requested address is A.
VERDICTS = [
    (200, A, posting("Robot Welder"), "Robot Welder", "custom", "read"),
    (None, A, posting("Robot Welder"), "Robot Welder", "custom", "read"),
    (200, A, posting("Senior Robot Welder (Stage)"), "Robot Welder", "custom", "read"),
    (200, A, posting("robot  welder"), "Robot Welder", "custom", "read"),  # case, spacing
    (200, job(1) + "?ref=list", posting("Robot Welder"), "Robot Welder", "custom", "read"),
    # Another page than its own, though it shows the title: never read (review, item 3).
    (
        200,
        START + "careers/robot-welder",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "unread",
    ),
    (200, START + "search?q=robot", LIST_PAGE, "Robot Welder", "custom", "unread"),
    (200, job(2), posting("Robot Welder"), "Robot Welder", "custom", "unread"),
    (
        200,
        "https://other.example.test/jobs/1",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "unread",
    ),
    (200, A, HINTED, "Robot Welder", "custom", "read"),  # a closure naming nothing: read
    (200, A, posting("Robot Welder", HINTED_BODY), "Robot Welder", "custom", "read"),
    # A wall is never read, whatever its headings or its text hold (review, item 4).
    (200, A, TITLED_SIGNIN, "Robot Welder", "custom", "walled"),
    (200, A, TITLED_SIGNIN + "\n" + BODY, "Robot Welder", "custom", "walled"),
    (200, A, TITLED_CHECK, "Robot Welder", "custom", "walled"),
    (200, A, GUEST, "Robot Welder", "linkedin", "walled"),
    (403, A, GUEST, "Robot Welder", "custom", "walled"),
    # Under service.TEXT_MIN_CHARS of text on its own address: a shell whose content was not
    # there yet, held as a page that did not load (round 3, item 4).
    (200, A, SHORT, "Robot Welder", "custom", "no_page"),
    (200, A, '- heading "Robot Welder" [level=1]', "Robot Welder", "custom", "no_page"),
    (200, A, "- paragraph: Loading", "Robot Welder", "custom", "no_page"),
    (200, A, posting("Robot Welder", SHORT_BODY), "Robot Welder", "custom", "no_page"),
    (200, A, f"{HEADER}\n{FOOTER}", "Robot Welder", "custom", "no_page"),  # the chrome alone
    (401, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (403, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (429, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (
        200,
        START + "login?next=/jobs/1",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "walled",
    ),
    (
        200,
        START + "checkpoint/challenge",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "walled",
    ),
    (200, A, CHECK, "Robot Welder", "custom", "walled"),
    (200, A, SIGNIN, "Robot Welder", "custom", "walled"),
    (200, "https://www.linkedin.test/authwall?trk=x", SIGNIN, "Robot Welder", "linkedin", "walled"),
    (404, A, "", "Robot Welder", "custom", "gone"),
    (410, A, "", "Robot Welder", "custom", "gone"),
    (200, A, CLOSED, "Robot Welder", "custom", "gone"),  # its page says it is closed
    (404, START + "search?q=robot", "", "Robot Welder", "custom", "unread"),  # not its own page
    (200, START + "search?q=robot", ELSEWHERE, "Robot Welder", "custom", "unread"),
    # A page that did not load: held (review of PR 864, round 2, item 2).
    (500, A, "", "Robot Welder", "custom", "no_page"),
    (503, A, posting("Robot Welder"), "Robot Welder", "custom", "no_page"),
    (200, A, "", "Robot Welder", "custom", "no_page"),  # a page of no text
    (200, A, '- link "Home" [ref=e1]:\n  - /url: /', "Robot Welder", "custom", "no_page"),
    (400, A, "", "Robot Welder", "custom", "unread"),  # an error that is no wall nor gone
    # On its own address, its one primary heading sharing a meaningful word with the title is
    # B21's rule for a report (`primary_heading`): read, as B27 reads it (declared, round 3).
    (200, A, posting("Robot Arm Engineer"), "Robot Welder", "custom", "read"),
    (200, A, posting("Robot"), "Robot Welder", "custom", "read"),
    (200, A, posting("Robot Welders Guild"), "Robot Welder", "custom", "read"),
    # No heading or line of it holds the title, and no primary heading shares a word with it.
    (200, A, posting("Example Painter"), "Robot Welder", "custom", "unread"),
    (200, A, bare("Example Painter"), "Robot Welder", "custom", "unread"),
    (200, A, posting("Welder", BODY), "Robot Painter", "custom", "unread"),
    (200, "", "", "Robot Welder", "custom", "no_page"),  # the page did not load
    # Its own address redirects to the home page, a search page or another posting's page.
    (200, START, HOME_PAGE, "Robot Welder", "custom", "unread"),
    (200, START + "search?q=robot+welder", HEADED_LIST, "Robot Welder", "custom", "unread"),
    (200, job(2), ARTICLE, "Robot Welder", "custom", "unread"),
    # Posting pages as websites lay them out, with their header, links and footer (round 3):
    # read on every platform, as B27 reads them for a report.
    *[(200, A, layout, "Robot Welder", "custom", "read") for layout in LAYOUTS.values()],
    (200, A, LINKEDIN, "Robot Welder", "linkedin", "read"),
    (200, A, posting("Robot Welder"), "Robot Welder", "indeed", "read"),
    (200, A, bare("Robot Welder"), "Robot Welder", "custom", "read"),
    (200, A, posting("ROBOT  WELDER"), "Robot Welder H/F", "custom", "read"),  # a gender marker
    (200, A, posting("Développeur Robot"), "developpeur robot", "custom", "read"),  # accents
    (200, A, posting("Robot", f"- paragraph: Welder.\n{BODY}"), "Robot Welder", "custom", "read"),
    # Nothing reads the page's shape: a list at the posting's own address whose heading holds
    # its title is read as the posting (declared, round 3, item 2); under TEXT_MIN_CHARS of
    # its own text, one is held.
    (200, A, HEADED_LIST, "Robot Welder", "custom", "read"),
    (200, A, LIST_PAGE, "Robot Welder", "custom", "no_page"),
]


@pytest.mark.parametrize(
    ("status", "landed", "snapshot", "title", "platform", "expected"), VERDICTS
)
def test_what_a_page_answers(status, landed, snapshot, title, platform, expected) -> None:
    read = bool(landed) and (status is None or status < 400)
    page = page_of(landed, snapshot) if read else None
    opened = finder.AppOpen(landed, status, read, True, True)
    found, closed = listed_pages.verdict(opened, A, page, snapshot, title, platform)
    assert found == expected
    assert (closed is not None) == (expected == "gone")


ID = job(12345)
HTTP = ID.replace("https://", "http://")
SLUG = ID + "-robot-welder"
WWW = ID.replace("//", "//www.")
OTHER_POSTING = posting("Robot Painter")
LONG_LIST = '- heading "Robot jobs in Paris" [level=1]\n' + cards(6, snippet=BODY[13:200])
# A page the app landed on after a redirect: (asked, landed, snapshot, title, verdict). It is
# read only on the posting's id kept as a whole piece of the landed path, and on the page's one
# level 1 heading holding the whole title (review of PR 864, round 4, follow up 1).
REDIRECTS = [
    (HTTP, ID, posting("Robot Welder"), "Robot Welder", "read"),
    (ID, WWW, posting("Robot Welder"), "Robot Welder", "read"),
    (WWW, ID, posting("Robot Welder"), "Robot Welder", "read"),
    (ID, ID + "/", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, ID + "-robot-welder", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, START + "jobs/robot-welder-12345", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, START + "fr/jobs/12345", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, ID + "?utm_source=x&gclid=1", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, SLUG + "/?utm_medium=y", posting("Robot Welder"), "Robot Welder", "read"),
    (ID, SLUG, posting("ROBOT  WELDER H/F"), "Robot Welder", "read"),  # folded as title_on
    (ID, SLUG, posting("Senior Robot Welder (Stage)"), "Robot Welder", "read"),
    (
        START + "job?id=123456",
        START + "job?id=123456&view=2",
        posting("Robot Welder"),
        "Robot Welder",
        "read",
    ),  # the id kept by its own parameter, on the same path
    (
        START + "job?id=123456",
        START.replace("https://", "http://") + "job?id=123456",
        posting("Robot Welder"),
        "Robot Welder",
        "read",
    ),  # the id in the same parameter
    # The two measured cases of the review: another posting by a short id, and a list at an
    # address that keeps the id in its query.
    (START + "job?id=42", START + "job?id=43", OTHER_POSTING, "Robot Welder", "unread"),
    (ID, START + "jobs?expired=12345", LONG_LIST, "Robot Welder", "unread"),
    (ID, START + "jobs?expired=12345", posting("Robot Welder"), "Robot Welder", "unread"),
    (ID, START + "jobs/123456", OTHER_POSTING, "Robot Welder", "unread"),  # a longer number
    (ID, START + "jobs/123456", posting("Robot Welder"), "Robot Welder", "unread"),
    (ID, START + "jobs/12345678-robot-welder", posting("Robot Welder"), "Robot Welder", "unread"),
    (ID, START, HOME_PAGE + "\n" + BODY, "Robot Welder", "unread"),
    (ID, START + "search?q=robot+welder", HEADED_LIST, "Robot Welder", "unread"),
    (ID, START + "jobs/54321", posting("Robot Welder"), "Robot Welder", "unread"),  # no id
    # The id is kept but the page's one level 1 heading does not hold the whole title.
    (ID, SLUG, posting("Robot Arm Engineer"), "Robot Welder", "unread"),  # one shared word
    (ID, SLUG, posting("Robot"), "Robot Welder", "unread"),
    (ID, SLUG, posting("Welder"), "Robot Welder", "unread"),
    (ID, SLUG, posting("Robot Painter"), "Robot Welder", "unread"),
    (
        ID,
        SLUG,
        posting("Robot Welder").replace("[level=1]", "[level=2]"),
        "Robot Welder",
        "unread",
    ),  # a title in a level 2 heading
    (ID, SLUG, posting("Example Painter"), "Robot Welder", "unread"),
]


@pytest.mark.parametrize(("asked", "landed", "snapshot", "title", "expected"), REDIRECTS)
def test_a_redirect_is_read_only_on_the_id_and_the_whole_title(
    asked, landed, snapshot, title, expected
) -> None:
    page = page_of(landed, snapshot)
    opened = finder.AppOpen(landed, 200, True, True, True)
    found, _ = listed_pages.verdict(opened, asked, page, snapshot, title, "custom")
    assert found == expected


def test_a_page_at_the_address_asked_for_keeps_the_rule_of_the_address() -> None:
    """No redirect: a primary heading sharing one meaningful word with the title is still read
    (declared)."""
    snapshot = posting("Robot Arm Engineer")
    opened = finder.AppOpen(ID, 200, True, True, True)
    found, _ = listed_pages.verdict(opened, ID, page_of(ID, snapshot), snapshot, "Robot Welder",
                                    "custom")  # fmt: skip
    assert found == "read"


def test_a_redirect_that_writes_another_pages_text_stores_nothing(monkeypatch) -> None:
    """Round 4, follow up 1, measured: a posting by a short query id redirected to another
    posting's page, and one redirected to a list at an address keeping its id in its query,
    were read; now each is a failed reading, never a text nor a new address."""
    by_id, expired = job(12345), START + "jobs?expired=12345"
    short, other = START + "job?id=42", START + "job?id=43"
    board = Board(
        {expired: LONG_LIST, other: OTHER_POSTING},
        redirects={by_id: expired, short: other},
    )
    held = [
        sweep.Listed(uuid.UUID(int=n), url, "Robot Welder", "Example Robotics", "Paris")
        for n, url in ((1, by_id), (2, short))
    ]
    done, commits, _ = phase(monkeypatch, board, held)
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [(1, "unread"), (2, "unread")]
    assert all(p.finding is None for p in commits.saved)
    assert (done["read"], done["unread"], done["ended"]) == (0, 2, "done")


def test_a_list_the_session_read_is_never_read_as_the_posting() -> None:
    """Round 2, item 1: another address the session read before as a list is never the
    posting's page; its own address, which the session counts a list for its header and footer
    links alone, is read."""
    snapshot = posting("Robot Welder")
    opened = finder.AppOpen(A, 200, True, True, True)
    page = page_of(A, snapshot)
    found, _ = listed_pages.verdict(opened, A, page, snapshot, "Robot Welder", "custom", lists={A})
    assert found == "read"
    view = A + "?view=list"
    moved = finder.AppOpen(view, 200, True, True, True)
    found, _ = listed_pages.verdict(
        moved, A, page_of(view, snapshot), snapshot, "Robot Welder", "custom", lists={view}
    )
    assert found == "unread"
    found, _ = listed_pages.verdict(moved, A, page_of(view, snapshot), snapshot, "Robot Welder",
                                    "custom")  # fmt: skip
    assert found == "read"


def test_a_status_that_is_no_http_status_is_a_page_that_did_not_load() -> None:
    snapshot = posting("Robot Welder")
    odd = finder.AppOpen(A, None, True, True, True, odd_status=True)
    found, _ = listed_pages.verdict(
        odd, A, page_of(A, snapshot), snapshot, "Robot Welder", "custom"
    )
    assert found == "no_page"


LI = "http://www.linkedin.test:8080/jobs/view/1234567890/"
IN = "http://fr.indeed.test:8080/viewjob?jk=0123456789abcdef"
# Whether the page the app landed on is the posting's own: (requested, landed, own).
OWN_PAGES = [
    (A, A, True),
    (A, A + "?ref=list", True),
    (A, A + "/", True),
    (A, job(2), False),
    (A, START + "search?q=robot+welder", False),
    (A, START + "jobs", False),
    (A, "https://other.example.test/jobs/1", False),
    (LI, LI, True),
    (LI, LI + "?trk=public_jobs", True),
    (LI, "http://www.linkedin.test:8080/jobs/view/robot-welder-at-example-1234567890/", True),
    (LI, "http://www.linkedin.test:8080/jobs/search/?currentJobId=1234567890", False),
    (LI, "http://www.linkedin.test:8080/jobs/view/1234567891/", False),
    (LI, "http://www.linkedin.test:8080/feed/", False),
    (IN, IN + "&from=serp", True),
    (IN, "http://fr.indeed.test:8080/viewjob?jk=fedcba9876543210", False),
    (IN, "http://fr.indeed.test:8080/cmp/example/jobs?jk=0123456789abcdef", False),
    (IN, "http://fr.indeed.test:8080/jobs?q=robot&vjk=0123456789abcdef", False),
    ("http://fr.indeed.test:8080/viewjob", "http://fr.indeed.test:8080/viewjob", False),  # no id
]


@pytest.mark.parametrize(("requested", "landed", "own"), OWN_PAGES)
def test_the_posting_own_page(requested: str, landed: str, own: bool) -> None:
    assert listed_pages.own_page(requested, landed) is own


WTTJ = "http://www.welcometothejungle.test:8080/"
INDEED = "http://www.indeed.test:8080/"
# A posting's address on a website's own hosts (finder.lease_hosts): (address, start, on).
ON_HOSTS = [
    (job(1), START, True),
    ("http://jobs.example.test/jobs/1", START, True),
    ("https://JOBS.example.test/jobs/1", START, True),
    ("https://sub.jobs.example.test/jobs/1", START, False),
    ("https://example.test/jobs/1", START, False),
    # Round 2, follow up 3: its `www.` name and its bare name are the same host.
    ("https://www.jobs.example.test/jobs/1", START, True),
    ("https://WWW.jobs.example.test/jobs/1", START, True),
    ("https://example-robotics.test/j/1", "https://www.example-robotics.test/", True),
    ("https://www.example-robotics.test/j/1", "https://example-robotics.test/", True),
    ("https://www.www.example-robotics.test/j/1", "https://example-robotics.test/", False),
    ("https://careers.example-robotics.test/j/1", "https://www.example-robotics.test/", False),
    ("https://wwwexample-robotics.test/j/1", "https://example-robotics.test/", False),
    ("https://ats.example-hire.test/j/1", "https://www.example-robotics.test/", False),
    ("https://www.other-board.test/viewjob?jk=1", START, False),
    ("https://jobs.example.test.evil.test/jobs/1", START, False),
    ("", START, False),
    ("javascript:alert(1)", START, False),
    (IN, INDEED, True),
    ("http://indeed.test:8080/viewjob?jk=0123456789abcdef", INDEED, True),
    ("http://a.b.indeed.test:8080/viewjob", INDEED, False),
    ("http://fra.indeed.test:8080/viewjob", INDEED, False),
    ("https://fr.indeed.test/viewjob", INDEED, False),
    (LI, INDEED, False),
    (WTTJ + "fr/companies/example/jobs/robot-welder", WTTJ, True),
    ("http://welcometothejungle.test:8080/fr/x", WTTJ, True),
    ("http://fr.welcometothejungle.test:8080/fr/x", WTTJ, False),
]


@pytest.mark.parametrize(("url", "start", "on"), ON_HOSTS)
def test_a_posting_on_the_websites_own_hosts(url: str, start: str, on: bool) -> None:
    assert finder.on_lease_hosts(url, start) is on


class Board(TabSite):
    """browserd's tabs over the website's pages; `statuses` the HTTP status a navigate answers,
    `closes` the addresses the memory guard closes the tab on."""

    def __init__(self, pages: dict[str, str], **kw: Any) -> None:
        self.statuses: dict[str, int] = kw.pop("statuses", {})
        self.closes: set[str] = set(kw.pop("closes", ()))
        # The addresses whose navigate loads after the memory guard closed the app's tab during
        # its pause (browserd opens it again and names the close in its answer).
        self.reopens: set[str] = set(kw.pop("reopens", ()))
        # Where a navigate lands instead of the address asked for (a redirect).
        self.redirects: dict[str, str] = kw.pop("redirects", {})
        # browserd's answer to `browser_tabs new` when it makes no tab, or the error it raises.
        self.no_tab: Any = kw.pop("no_tab", None)
        super().__init__({kw.pop("start", START): HOME, **pages}, **kw)

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        if name == "browser_tabs" and args.get("action") == "new" and self.no_tab is not None:
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.calls.append((name, dict(args)))
            if isinstance(self.no_tab, Exception):
                raise self.no_tab
            return self.no_tab
        if name == "browser_navigate" and url in self.closes:
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.calls.append((name, dict(args)))
            close = {"id": "0000beef", "reason": "memory", "used_mib": 1870}
            return {"ok": False, "error": "tab_closed", "tab_closed": close}
        if name == "browser_navigate" and url in self.redirects:
            answer = await super().call(name, {**args, "url": self.redirects[url]})
            self.calls[-1] = (name, {**self.calls[-1][1], "url": url})
        else:
            answer = await super().call(name, args)
        if name == "browser_navigate" and answer.get("ok") and url in self.statuses:
            answer["status"] = self.statuses[url]
        if name == "browser_navigate" and answer.get("ok") and url in self.reopens:
            answer["tab_closed"] = {"id": "0000cafe", "reason": "memory", "used_mib": 1900}
        return answer

    def opened(self) -> list[str]:
        return [a["url"] for n, a in self.calls if n == "browser_navigate"]


class Policy:
    """The website's permission (B14's InteractivePolicy): taken back once `revoked`."""

    def __init__(self) -> None:
        self.revoked = False
        self.checks = 0

    async def check(self, context: Any) -> str | None:
        self.checks += 1
        return "permission_revoked" if self.revoked else None

    async def reserve(self, context: Any) -> str | None:
        return None

    async def observe(self, context: Any, url: str, snapshot: str) -> str | None:
        return None


class Commits:
    """The run's commits as the phase uses them: what was saved, a store that fails at a
    given save, the postings whose failed reading is their last try."""

    def __init__(
        self,
        fail_at: int | None = None,
        last_try: set[uuid.UUID] | None = None,
        left_as_is: set[uuid.UUID] | None = None,
    ):
        self.saved: list[sweep.ListedPage] = []
        self.fail_at, self.last_try = fail_at, last_try or set()
        self.left_as_is = left_as_is or set()  # postings the store leaves as they are
        self.unsettled = False
        self.sessions = None
        self.after: Any = None
        self.clock = lambda: TODAY

    async def save(self, source_id, source, batch, set_aside=(), listed=None) -> None:
        assert (source_id, source, batch, set_aside) == ("src", HOST, None, ())
        if self.fail_at == len(self.saved):
            raise sweep.StoreRefusedError
        done = {"read": "read", "gone": "closed", "unread": "unread"}[listed.outcome]
        listed.done = "" if listed.job_id in self.left_as_is else done
        listed.given_up = listed.done == "unread" and listed.job_id in self.last_try
        self.saved.append(listed)
        if self.after is not None:
            self.after(len(self.saved))


class Leases:
    def __init__(self) -> None:
        self.waiting: list[tuple[str, str]] = []
        self.gone = False

    async def waiters(self) -> list[tuple[str, str]]:
        if self.gone:
            raise StoreUnavailableError
        return self.waiting


def listed(n: int, title: str = "Robot Welder") -> sweep.Listed:
    return sweep.Listed(uuid.UUID(int=n), job(n), title, "Example Robotics", "Paris, France")


def phase(
    monkeypatch,
    board: Board,
    held: list[sweep.Listed],
    *,
    commits: Commits | None = None,
    leases: Leases | None = None,
    policy: Policy | None = None,
    bound_s: float = 600.0,
    stopped: str = "",
    ran: str = "",
    carded: tuple[int, ...] = (),
    step_s: float = 0.0,
    tried: set[uuid.UUID] | None = None,
    lists: tuple[str, ...] = (),
) -> tuple[dict[str, Any] | None, Commits, dict[str, Any]]:
    """The website's reading of pages after its agent, on `board`, for the postings `held`."""
    clock = [0.0]
    asked: dict[str, Any] = {}

    def postings(sessions, user_id, host, on_site, first, day):
        asked.update(host=host, first=set(first), on_site=on_site, day=day)
        return list(held)

    def left(sessions, user_id, host, on_site):
        asked.update(left_on_site=on_site)
        return 7

    monkeypatch.setattr(sweep, "listed_postings", postings)
    monkeypatch.setattr(sweep, "listed_left", left)

    async def sleep(seconds: float) -> None:
        asked.setdefault("pauses", []).append(seconds)
        clock[0] += seconds

    def monotonic() -> float:
        clock[0] += step_s
        return clock[0]

    context = interactive.SourceContext("e2e-person", "sweep-1", "src", "custom", START)
    tools = interactive.GuardedTools(board, policy or Policy(), context)
    tools.reserved = True
    pages = sources.Pages(
        tools, math.inf, sleep=sleep, rng=random.Random(5), monotonic=monotonic, pause=(0.0, 0.0),  # noqa: S311  deterministic test pacing
    )  # fmt: skip
    scope = finder.InteractiveScope(START, ("robot",), (), ())
    one = finder.FinderSession(pages, START, HOME, "e2e-person", interactive=scope)
    one.stopped = stopped
    one.lists.update(dict.fromkeys(lists))  # the lists the agent's session read
    site = interactive._Site(
        interactive.Searches(scope, unwanted_titles=re.compile(r"\bstage\b", re.IGNORECASE)),
        source_id="src",
        host=HOST,
    )
    commits = commits or Commits()
    plan = SimpleNamespace(
        commits=commits,
        user_id=uuid.UUID(int=99),
        known=SimpleNamespace(carded={uuid.UUID(int=n) for n in carded}),
        listed_tried=set() if tried is None else tried,
    )
    runner = SimpleNamespace(
        monotonic=monotonic, listed_pages_s=bound_s, leases=leases or Leases(), base_url=None
    )
    asyncio.run(listed_pages.read(one, ran, site=site, runner=runner, plan=plan))
    asked["session"] = one
    return site.listed_pages, commits, asked


def test_pages_that_read_a_wall_and_a_gone_page(monkeypatch) -> None:
    """Acceptance 1 to 3: one page at a time in a tab of its own, 2 to 4 s apart, the agent's
    tab selected again; read, gone and failed stored; a wall is held as a page that did not
    load is (round 3, item 3), and the second wall in a row ends the reading with neither
    stored."""
    board = Board(
        {
            job(1): posting("Robot Welder"),
            job(2): "",
            job(3): ELSEWHERE,
            job(4): SIGNIN,
            job(5): CHECK,
            job(6): posting("Robot Welder"),
        },
        statuses={job(2): 404},
    )
    held = [listed(n) for n in range(1, 7)]
    done, commits, asked = phase(monkeypatch, board, held, carded=(3,))
    assert done == {
        "read": 1,
        "closed": 1,
        "unread": 1,
        "given_up": 0,
        "ended": "walled",
        "left": 7,
    }
    assert [(p.job_id.int, p.outcome, p.reason) for p in commits.saved] == [
        (1, "read", ""), (2, "gone", ""), (3, "unread", ""),
    ]  # fmt: skip
    assert board.opened() == [job(n) for n in range(1, 6)]  # never the one after the walls
    pauses = asked["pauses"]  # B20: no page pause of the website's own (pause_for: none)
    assert len(pauses) == 5
    assert all(2.0 <= p <= 4.0 for p in pauses)
    tabs = [a["action"] for n, a in board.calls if n == "browser_tabs"]
    assert tabs == ["new", "close", "select"] * 5
    assert board.tabs == [""]  # the agent's tab alone, selected
    read = commits.saved[0].finding
    assert (read.title, read.url, read.list_url) == ("Robot Welder", job(1), None)
    assert "invented robot arms" in read.text
    assert "Privacy" not in read.text  # the footer is no part of the posting
    assert commits.saved[1].closed.verdict == "closed"
    assert asked["host"] == HOST
    assert asked["first"] == {uuid.UUID(int=3)}
    assert asked["day"] == date(2026, 11, 28)  # the day of the order, in Europe/Paris
    for on_site in (asked["on_site"], asked["left_on_site"]):  # the website's own hosts only
        assert on_site(job(1)) is True
        assert on_site("https://www.other-board.test/viewjob?jk=1") is False
    assert asked["session"].opened_by_app == 0  # B27's counter counts the agent's reports only
    assert job(1) in asked["session"].lists  # its header and footer links alone make it one


def test_a_wall_then_a_page_that_loads_then_a_wall_go_on(monkeypatch) -> None:
    """Round 2, follow up 1 and round 3, item 3: walls apart are each a failed reading with
    its reason, stored once a later page answers otherwise; a wall on one posting's page (or a
    false wall) never blocks the later ones."""
    board = Board(
        {job(1): SIGNIN, job(2): ELSEWHERE, job(3): CHECK, job(4): posting("Robot Welder")}
    )
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, 5)])
    assert [(p.outcome, p.reason) for p in commits.saved] == [
        ("unread", "walled"), ("unread", ""), ("unread", "walled"), ("read", ""),
    ]  # fmt: skip
    assert (done["read"], done["unread"], done["ended"]) == (1, 3, "done")


def test_a_website_whose_pages_do_not_load_stores_nothing(monkeypatch) -> None:
    """Round 2, item 2: a network or an egress down (browserd's navigation_failed) for the
    whole reading: three pages in a row, none stored, the reading ends `no_page`."""
    board = Board({})
    done, commits, asked = phase(monkeypatch, board, [listed(n) for n in range(1, 8)])
    assert commits.saved == []
    assert board.opened() == [job(1), job(2), job(3)]
    assert done == {
        "read": 0, "closed": 0, "unread": 0, "given_up": 0, "ended": "no_page", "left": 7,
    }  # fmt: skip
    assert len(asked["pauses"]) == 3


def test_a_server_error_an_odd_status_and_an_empty_page_are_held_too(monkeypatch) -> None:
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder"), job(3): ""},
        statuses={job(1): 503, job(2): 999},
    )
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, 5)])
    assert commits.saved == []
    assert done["ended"] == "no_page"
    assert board.opened() == [job(1), job(2), job(3)]


def test_a_page_that_did_not_load_is_stored_once_a_later_one_loads(monkeypatch) -> None:
    """Round 2, item 2: a page that does not load between two that read: three stored, in
    order, the middle one a failed reading; two in a row before a page that loads, too."""
    board = Board({job(1): posting("Robot Welder"), job(3): posting("Robot Welder")})
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2), listed(3)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [
        (1, "read"), (2, "unread"), (3, "read"),
    ]  # fmt: skip
    assert (done["read"], done["unread"], done["ended"]) == (2, 1, "done")
    board = Board({job(3): ELSEWHERE})
    _, commits, _ = phase(monkeypatch, board, [listed(1), listed(2), listed(3)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [
        (1, "unread"), (2, "unread"), (3, "unread"),
    ]  # fmt: skip


@pytest.mark.parametrize("last", ["done", "walled", "time_bound"])
def test_pages_held_when_the_reading_ends_another_way_are_not_stored(monkeypatch, last) -> None:
    board = Board({job(1): posting("Robot Welder"), job(3): SIGNIN, job(4): SIGNIN})
    held = {
        "done": [listed(1), listed(2)],
        "walled": [listed(1), listed(3), listed(2), listed(4)],
        "time_bound": [listed(1), listed(2), listed(5)],
    }[last]
    done, commits, _ = phase(
        monkeypatch, board, held, bound_s=20.0 if last == "time_bound" else 600.0, step_s=1.0
    )
    assert done["ended"] == last
    assert listed(2).id not in [p.job_id for p in commits.saved]


def test_a_redirect_to_a_list_the_session_read_before_stays_unread(monkeypatch) -> None:
    """Round 2, item 1: the posting's address redirects to an address the agent's session read
    as a list before the reading, whatever that page now shows; the posting's own address,
    read before by the agent, is read."""
    view = job(1) + "?view=list"
    board = Board({view: posting("Robot Welder"), job(2): posting("Robot Welder")},
                  redirects={job(1): view})  # fmt: skip
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)], lists=(view, job(2)))
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [(1, "unread"), (2, "read")]
    assert done["ended"] == "done"


def test_a_list_at_its_own_address_headed_with_its_title_is_read_as_declared(monkeypatch) -> None:
    """Round 3, item 2, declared: nothing reads the page's shape, so a list of results served
    at the posting's own address whose heading holds its title is read as the posting, as B27
    reads such a page for a report; served at another address, it is a failed reading."""
    search = START + "search?q=robot+welder"
    board = Board({job(1): HEADED_LIST, search: HEADED_LIST}, redirects={job(2): search})
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [(1, "read"), (2, "unread")]
    assert commits.saved[0].finding.title == "Robot Welder jobs in Paris"
    assert (done["read"], done["unread"], done["ended"]) == (1, 1, "done")


def test_the_layouts_of_real_posting_pages_read(monkeypatch) -> None:
    """Round 3: the posting pages as websites lay them out (an article with its company's link,
    sibling sections, LinkedIn like, no `main`, bullets, a short posting with 8 similar postings
    and a cookie banner) are read through the reading, each its own posting."""
    names = list(LAYOUTS)
    board = Board({job(n): LAYOUTS[name] for n, name in enumerate(names, 1)})
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, len(names) + 1)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [
        (n, "read") for n in range(1, len(names) + 1)
    ]
    assert {p.finding.title for p in commits.saved} == {"Robot Welder"}
    assert (done["read"], done["unread"], done["ended"]) == (len(names), 0, "done")


def test_a_website_walled_from_its_first_page_gives_up_nothing(monkeypatch) -> None:
    """Round 3, item 3: walls are held as pages that did not load: two in a row end the reading
    `walled` with neither stored, whatever their headings hold, so no posting takes a failed
    reading for the website's wall."""
    board = Board({job(n): SIGNIN if n % 2 else TITLED_CHECK for n in range(1, 6)})
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, 6)])
    assert commits.saved == []
    assert board.opened() == [job(1), job(2)]
    assert done == {
        "read": 0, "closed": 0, "unread": 0, "given_up": 0, "ended": "walled", "left": 7,
    }  # fmt: skip
    statuses = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)},
                     statuses={job(1): 429, job(2): 403})  # fmt: skip
    done, commits, _ = phase(monkeypatch, statuses, [listed(n) for n in (1, 2, 3)])
    assert (commits.saved, done["ended"]) == ([], "walled")


def test_a_page_that_did_not_load_between_two_walls_ends_the_reading(monkeypatch) -> None:
    """Round 3, item 3: a page that did not load says nothing of the website's wall, so a wall,
    such a page and a wall end the reading `walled`, none of the three stored; a wall and pages
    that did not load are stored, in order, once a later page answers otherwise."""
    board = Board({job(1): SIGNIN, job(3): CHECK, job(4): posting("Robot Welder")})
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, 5)])
    assert (commits.saved, done["ended"]) == ([], "walled")
    assert board.opened() == [job(1), job(2), job(3)]
    board = Board({job(1): SIGNIN, job(4): posting("Robot Welder")})
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in range(1, 5)])
    assert [(p.job_id.int, p.outcome, p.reason) for p in commits.saved] == [
        (1, "unread", "walled"), (2, "unread", ""), (3, "unread", ""), (4, "read", ""),
    ]  # fmt: skip
    assert (done["unread"], done["ended"]) == (3, "done")


def test_a_page_of_too_little_text_is_held_as_an_empty_page_is(monkeypatch) -> None:
    """Round 3, item 4: on its own address, under service.TEXT_MIN_CHARS characters of text (a
    shell whose content was not there yet) is held: stored as a failed reading only once a
    later page answers, never when it is the last, and three in a row end the reading."""
    board = Board({job(1): SHORT, job(2): posting("Robot Welder")})
    _, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [(1, "unread"), (2, "read")]
    board = Board({job(1): posting("Robot Welder"), job(2): f"{HEADER}\n{FOOTER}"})
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [(1, "read")]
    assert done["ended"] == "done"
    board = Board({job(n): SHORT for n in (1, 2, 3, 4)})
    done, commits, _ = phase(monkeypatch, board, [listed(n) for n in (1, 2, 3, 4)])
    assert (commits.saved, done["ended"]) == ([], "no_page")


def test_what_redirects_and_soft_errors_store(monkeypatch) -> None:
    """Round 2's attacks on item 3 of round 1, on posting pages that hold links: another
    posting, the home page, a list at its own address, two soft 404s; the same path with
    another short query id and the same title is read (declared)."""
    other, same_path = START + "jobs/12346", START + "job?id=34"
    soft = posting("Robot Welder", f"- paragraph: This job is no longer available.\n{BODY}")
    board = Board(
        {
            other: posting("Robot Welder"),
            same_path: posting("Robot Welder"),
            job(4): LIST_PAGE,
            job(5): soft,
            job(6): '- heading "Page not found" [level=1]\n- paragraph: Sorry.',
        },
        redirects={job(1): other, job(2): START, START + "job?id=12": same_path},
    )
    short_id = sweep.Listed(
        uuid.UUID(int=3), START + "job?id=12", "Robot Welder", "Example Robotics", "Paris, France"
    )
    held = [listed(1), listed(2), short_id, listed(4), listed(5), listed(6)]
    _, commits, _ = phase(monkeypatch, board, held)
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [
        (1, "unread"), (2, "unread"), (3, "read"), (4, "unread"), (5, "gone"), (6, "gone"),
    ]  # fmt: skip
    assert commits.saved[2].finding.url == same_path


def test_a_read_page_meets_the_persons_exclusions(monkeypatch) -> None:
    board = Board({job(1): posting("Robot Welder (Stage)")})
    done, commits, _ = phase(monkeypatch, board, [listed(1)])
    assert done["read"] == 1
    assert done["ended"] == "done"
    assert commits.saved[0].excluded == "Unwanted title: Stage"


def test_a_failed_reading_counts_its_last_try(monkeypatch) -> None:
    board = Board({job(1): ELSEWHERE, job(2): ELSEWHERE})
    done, _, _ = phase(
        monkeypatch, board, [listed(1), listed(2)], commits=Commits(last_try={uuid.UUID(int=2)})
    )
    assert (done["unread"], done["given_up"], done["ended"]) == (2, 1, "done")


def test_a_close_for_memory_ends_the_reading(monkeypatch) -> None:
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, closes={job(1)}
    )
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2)])
    assert done["ended"] == "memory"
    assert commits.saved == []  # the browser's close is no failed reading of the posting
    assert board.opened() == [job(1)]
    assert asked["session"].tab_closed["memory"] == 1


def test_a_permission_taken_back_in_the_middle_ends_it_and_keeps_nothing_more(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)})
    policy, commits = Policy(), Commits()
    commits.after = lambda saved: setattr(policy, "revoked", True)
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2), listed(3)], commits=commits, policy=policy
    )
    assert done["ended"] == "permission"
    assert len(commits.saved) == 1
    assert board.opened() == [job(1)]


def test_a_permission_taken_back_during_a_page_keeps_nothing_of_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    policy = Policy()
    original = board.call

    async def revoking(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_navigate" and args.get("url") == job(2):
            policy.revoked = True
        return await original(name, args)

    board.call = revoking  # type: ignore[method-assign]
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)], policy=policy)
    assert done["ended"] == "permission"
    assert [p.job_id.int for p in commits.saved] == [1]


def test_an_application_waiting_for_the_browser_comes_first(monkeypatch) -> None:
    leases = Leases()
    commits = Commits()
    commits.after = lambda saved: leases.waiting.append(("e2e-other", "apply_fill"))
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2)], commits=commits, leases=leases
    )
    assert done["ended"] == "browser_wanted"
    assert board.opened() == [job(1)]


def test_a_lease_store_that_cannot_say_counts_as_one_waiting(monkeypatch) -> None:
    leases = Leases()
    leases.gone = True
    board = Board({job(1): posting("Robot Welder")})
    done, _, _ = phase(monkeypatch, board, [listed(1)], leases=leases)
    assert done["ended"] == "browser_wanted"
    assert board.opened() == []


def test_the_time_bound_starts_no_page_that_could_end_past_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in range(1, 30)})
    held = [listed(n) for n in range(1, 30)]
    done, commits, _ = phase(monkeypatch, board, held, bound_s=120.0, step_s=10.0)
    assert done["ended"] == "time_bound"
    assert 0 < len(commits.saved) < len(held)
    nothing, none, _ = phase(monkeypatch, Board({}), held, bound_s=0.0)
    assert nothing is None
    assert none.saved == []


def test_a_failed_store_ends_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)], commits=Commits(fail_at=0))
    assert done["ended"] == "store_failed"
    assert commits.saved == []
    assert board.opened() == [job(1)]
    ended = Commits()
    ended.unsettled = True  # the sweep let go of its row
    done, _, _ = phase(monkeypatch, Board({}), [listed(1)], commits=ended)
    assert done["ended"] == "store_failed"


def test_a_lease_ended_during_a_page_ends_it(monkeypatch) -> None:
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, end_after=None
    )
    original = board.call

    async def ending(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_tabs" and args.get("action") == "new" and len(board.opened()) == 1:
            from app.browser.client import BrowserError

            raise BrowserError("ended")
        return await original(name, args)

    board.call = ending  # type: ignore[method-assign]
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert done["ended"] == "browser_lost"
    assert [p.outcome for p in commits.saved] == ["read"]


@pytest.mark.parametrize(
    ("stopped", "ran", "runs"),
    [
        ("", "", True),
        ("", "error", True),  # the agent's run failed: the browser and the lease are there
        ("", "turn_limit", True),
        ("token_budget", "", True),
        ("browser_memory", "", True),
        ("page_failed", "", True),
        ("", "cancelled", False),  # the person's Cancel
        ("", "no_credential", False),
        ("lease_ended", "", False),
        ("browser_unavailable", "", False),
        ("guard_failed", "", False),
        ("permission_revoked", "", False),
        ("store_failed", "", False),
    ],
)
def test_when_it_runs_after_the_agent(monkeypatch, stopped: str, ran: str, runs: bool) -> None:
    board = Board({job(1): posting("Robot Welder")})
    done, commits, asked = phase(monkeypatch, board, [listed(1)], stopped=stopped, ran=ran)
    assert (done is not None) == runs
    assert len(commits.saved) == runs
    assert asked["session"].stopped == stopped  # the agent's stop stays the website's


@pytest.mark.parametrize(
    "no_tab",
    [
        {"ok": False, "error": "too_many_tabs"},  # the agent left its 3 tabs open
        BrowserError("busy"),
        {"ok": False, "error": "person_input"},  # the person holds the input
        {"ok": True, "tabs": [{"index": 0, "url": START, "current": True}]},  # a tab it cannot name
    ],
)
def test_no_tab_of_its_own_stores_nothing_and_ends_the_reading(monkeypatch, no_tab) -> None:
    """Review of PR 864, item 1: browserd made no tab, so no page was opened: nothing is stored
    against any posting, and the reading ends with a reason the summary says."""
    board = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)}, no_tab=no_tab)
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2), listed(3)])
    assert done == {
        "read": 0, "closed": 0, "unread": 0, "given_up": 0, "ended": "no_tab", "left": 7,
    }  # fmt: skip
    assert commits.saved == []
    assert board.opened() == []
    assert [a["action"] for n, a in board.calls if n == "browser_tabs"].count("new") == 1
    assert "pauses" not in asked
    assert asked["session"].stopped == ""


def test_a_redirect_to_a_list_or_to_another_posting_is_never_read(monkeypatch) -> None:
    """Review of PR 864, item 3: the posting's address redirects to the website's search page
    listing its title, or to another posting's page holding it: no page is read as the
    posting, each is a failed reading (one posting alone too: no page is held)."""
    search = START + "search?q=robot+welder"  # test_b20's Site serves its own `search?q=robot`
    board = Board(
        {search: LIST_PAGE, job(9): posting("Robot Welder")},
        redirects={job(1): search, job(2): job(9)},
    )
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert [(p.outcome, p.finding) for p in commits.saved] == [("unread", None)] * 2
    assert (done["read"], done["unread"], done["ended"]) == (0, 2, "done")
    assert board.opened() == [job(1), job(2)]
    _, alone, _ = phase(monkeypatch, board, [listed(1)])
    assert [(p.outcome, p.finding) for p in alone.saved] == [("unread", None)]


def test_a_close_for_memory_named_by_a_navigate_that_loaded_ends_the_reading(monkeypatch) -> None:
    """Review of PR 864, follow up 5: the memory guard closed the app's tab during its pause and
    the navigate that followed loaded the page: the close is counted and ends the reading."""
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, reopens={job(1)}
    )
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2)])
    assert asked["session"].tab_closed["memory"] == 1
    assert done["ended"] == "memory"
    assert board.opened() == [job(1)]
    assert [p.outcome for p in commits.saved] == ["read"]  # its page loaded and was read


def test_the_counts_are_what_the_store_did(monkeypatch) -> None:
    """Review of PR 864, item 5: a page the store left as it was (read meanwhile, its address
    changed, a posting the app closed before) is counted in nothing."""
    board = Board({job(1): posting("Robot Welder"), job(2): "", job(3): ELSEWHERE},
                  statuses={job(2): 404})  # fmt: skip
    kept = {uuid.UUID(int=n) for n in (1, 2, 3)}
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2), listed(3)], commits=Commits(left_as_is=kept)
    )
    assert [p.outcome for p in commits.saved] == ["read", "gone", "unread"]
    assert (done["read"], done["closed"], done["unread"], done["ended"]) == (0, 0, 0, "done")


def test_a_posting_no_tab_was_made_for_is_opened_by_the_next_website(monkeypatch) -> None:
    """Round 2, follow up 2: a posting counts as tried once a navigate was called for it."""
    tried: set[uuid.UUID] = set()
    first = Board({job(1): posting("Robot Welder")}, no_tab={"ok": False, "error": "too_many_tabs"})
    done, _, _ = phase(monkeypatch, first, [listed(1)], tried=tried)
    assert (done["ended"], tried) == ("no_tab", set())
    second = Board({job(1): posting("Robot Welder")})
    done, commits, _ = phase(monkeypatch, second, [listed(1)], tried=tried)
    assert second.opened() == [job(1)]
    assert [p.outcome for p in commits.saved] == ["read"]
    assert tried == {uuid.UUID(int=1)}


def test_two_websites_of_one_search_on_one_host_open_a_posting_once(monkeypatch) -> None:
    """Review of PR 864, follow up 6: two saved addresses of one website in one search: the
    second website's reading opens none of the postings the first opened."""
    tried: set[uuid.UUID] = set()
    first = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    phase(monkeypatch, first, [listed(1), listed(2)], tried=tried)
    second = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)})
    done, commits, _ = phase(monkeypatch, second, [listed(1), listed(2), listed(3)], tried=tried)
    assert first.opened() == [job(1), job(2)]
    assert second.opened() == [job(3)]
    assert [p.job_id.int for p in commits.saved] == [3]
    assert done["read"] == 1


def test_the_summary_knows_every_end() -> None:
    from app.jobs import sweep_router

    assert sweep_router.LISTED_ENDS == listed_pages.ENDS
