"""Browser B31 (owner, 2026-11-26: "Je veux vraiment qu'on remonte un max de job"): a report keeps
what the search saw. Another spelling of an address the session saw names the same posting
(`finder.posting_key`: one posting id of a board that has ids, else one address once the
parameters that only track are left out, one trailing slash aside), so the app opens it (B27) or
keeps it from its card (J31) as it would the seen one; a script's item giving a posting's id is
its card; past the opens of one call a card stands in, in the same call, and the answer says so.
An id or an address the session never saw stays refused. The search of 2026-11-27 replayed in
its shape: Indeed's list links `/rc/clk?jk=<key>&...` while the agent reported
`/viewjob?jk=<key>` (58 refused `not_seen`), LinkedIn signed in reported more postings than one
call opens (63 refused `app_opens_max`).

Real board hosts appear only in the pure tables (never contacted); the sessions run on the
boards' `.test` stand ins (`same_posting.FAMILIES`) and invented hosts. Invented postings only."""

import asyncio
import json
import random
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.browser import finder, same_posting, sources
from app.jobs import slug
from tests.browser.posting_pages import dressed

JK = "5f1c2d3e4a5b6c7d"  # an Indeed key's shape: 16 lowercase hex digits
JK2 = "0a1b2c3d4e5f6a7b"
LI = "4012345678"
CAREERS = "https://careers.example.org/jobs/robot-engineer-42"
# The shapes of real application systems, invented companies and ids (never contacted).
GH_PAGE = "https://www.example-robotics.test/careers?gh_jid=4012345"
GH_BOARD = "https://boards.greenhouse.io/examplerobotics/jobs/4012345"
LEVER = "https://jobs.lever.co/examplerobotics/0f1e2d3c-4b5a-4968-8778-000000000042"
WORKDAY = ("https://examplerobotics.wd3.myworkdayjobs.com/en-US/External/job/Bern/"
           "Data-Engineer_R12345")  # fmt: skip
WTTJ = "https://www.welcometothejungle.com/fr/companies/example-robotics/jobs/data-engineer_bern"


def key(url: str) -> str:
    return finder.posting_key(finder.canonical(url))


# --- the rule's accept table: each pair names one posting ----------------------------------------

INDEED_HOSTS = ["https://ch.indeed.com", "https://www.indeed.com", "https://indeed.com",
                "https://fr.indeed.com", "HTTPS://CH.Indeed.com:443"]  # fmt: skip
INDEED_PATHS = [
    f"/rc/clk?jk={JK}&bb=a1&xkcb=b2&fccid=c3&vjs=3&tk=d4&from=serp&advn=e5&adid=f6&sjdu=g7",
    f"/viewjob?jk={JK}&from=serp&vjs=3",
    f"/m/viewjob?jk={JK}",
    f"/cmp/Example-Robotics/jobs?jk={JK}",
    f"/viewjob?%6Ak={JK}",  # a name percent encoded is read as the server reads it
    f"/viewjob?jk=%35{JK[1:]}#apply",  # a value too, and the fragment aside
]
LINKEDIN_SPELLINGS = [
    f"https://www.linkedin.com/jobs/view/{LI}",
    f"https://www.linkedin.com/jobs/view/{LI}/?eBP=x1&refId=y2&trackingId=z3&trk=flagship3",
    f"https://ch.linkedin.com/jobs/view/data-engineer-at-example-robotics-{LI}?position=1&pageNum=0",
    f"https://www.linkedin.com/jobs/search/?currentJobId={LI}&keywords=data&geoId=1",
    f"https://www.linkedin.com/jobs/collections/recommended/?currentJobId={LI}",
    f"https://linkedin.com/jobs/view/{LI}/?currentJobId={LI}",  # the same id twice
    f"https://fr.linkedin.com/jobs/view/ing%C3%A9nieur-donn%C3%A9es-{LI}/",
]
GENERIC_SAME = [
    (CAREERS, CAREERS + "/"),  # one trailing slash aside
    (CAREERS, CAREERS + "?utm_source=board&utm_medium=mail&utm_campaign=x"),
    (CAREERS, CAREERS + "/?utm_content=y"),
    *[(CAREERS, f"{CAREERS}?{name}=abc") for name in sorted(same_posting.TRACKING)],
    (CAREERS, CAREERS + "#apply"),
    (CAREERS, "https://careers.example.org:443/jobs/robot-engineer-42"),
    (CAREERS, "HTTPS://Careers.Example.ORG/jobs/robot-engineer-42"),
    (CAREERS + "?id=42&lang=fr", CAREERS + "?lang=fr&id=42"),  # the order aside
    (CAREERS + "?id=42&lang=fr", CAREERS + "?lang=fr&utm_source=x&id=42"),
    (CAREERS + "?id=42", CAREERS + "?id=%34%32"),
    (CAREERS + "?id=42&&", CAREERS + "?id=42"),  # empty pieces are none
    # A board's own trackers, on its family only, for an address with no posting id.
    ("https://ch.indeed.com/cmp/Example-Robotics", "https://ch.indeed.com/cmp/Example-Robotics"
     "?from=serp&tk=abc&vjs=3"),
    ("https://www.linkedin.com/company/example-robotics/jobs",
     "https://www.linkedin.com/company/example-robotics/jobs/?trk=x&refId=y&trackingId=z&eBP=w"
     "&trkInfo=v&lipi=u"),
    # Application systems the rule has no ids for: their id identifies under the default, and
    # only a parameter that tracks may differ.
    (GH_PAGE, GH_PAGE + "&utm_source=board&gclid=x"),
    (GH_BOARD, GH_BOARD + "/?utm_medium=mail"),
    (LEVER, LEVER + "/?utm_campaign=x"),
    (WORKDAY, WORKDAY + "?utm_source=board"),
    (WTTJ, WTTJ + "?utm_source=x&utm_medium=y"),
]  # fmt: skip
SAME = (
    [(f"https://ch.indeed.com/viewjob?jk={JK}", h + p) for h in INDEED_HOSTS for p in INDEED_PATHS]
    + [(f"https://www.linkedin.com/jobs/view/{LI}/", one) for one in LINKEDIN_SPELLINGS]
    + GENERIC_SAME
)


@pytest.mark.parametrize(("seen", "reported"), SAME)
def test_two_spellings_of_one_posting_have_one_key(seen: str, reported: str) -> None:
    assert key(seen), seen
    assert key(seen) == key(reported)


# --- the refuse table: the ways an id is made up or borrowed, and the near misses ---------------

NOT_SAME = [
    # Indeed: a piece of the id, a longer one, another, another shape, two ids, another name.
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jk={JK[:-1]}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jk={JK}0"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jk={JK2}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jk={JK.upper()}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jk={JK}&jk={JK2}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?vjk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob?jobkey={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob/{JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.indeed.com/viewjob#jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", "https://ch.indeed.com/viewjob?jk="),
    # The id seen on another host family, or on a host that only looks like the board's.
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://jobs.example.org/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://indeed.com.example.org/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://notindeed.com/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://chx.indeed.com/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://ch.fr.indeed.com/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://uk.indeed.co.uk/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"http://ch.indeed.com/viewjob?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"https://www.linkedin.com/jobs/view/?jk={JK}"),
    (f"https://ch.indeed.com/viewjob?jk={JK}", f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
    # LinkedIn: a piece, a longer id, glued letters, a sub page, two ids, another parameter.
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/view/{LI[:-1]}"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/view/{LI}9"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/view/abc{LI}"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/view/{LI}-x"),
    (f"https://www.linkedin.com/jobs/view/{LI}",
     f"https://www.linkedin.com/jobs/view/{LI}/apply/"),
    (f"https://www.linkedin.com/jobs/view/{LI}",
     f"https://www.linkedin.com/jobs/view/{LI}/?currentJobId=4012345679"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/?jobId={LI}"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/jobs/?refId={LI}"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://www.linkedin.com/job/view/{LI}"),
    (f"https://www.linkedin.com/jobs/view/{LI}", f"https://careers.example.org/jobs/view/{LI}"),
    (f"https://www.linkedin.com/jobs/view/{LI}",
     "https://www.linkedin.com/jobs/view/40123456781234567890"),  # past the id's shape
    # Any other address: a parameter not listed identifies, whatever a board uses it for.
    *[(CAREERS, f"{CAREERS}?{name}=x") for name in
      ("ref", "from", "tk", "trk", "refId", "vjs", "page", "id", "lang", "utmsource", "UTM_source",
       "gclid_x")],
    (CAREERS, CAREERS + "//"),  # two trailing slashes
    (CAREERS, CAREERS.replace("/jobs/", "/Jobs/")),  # a path's case
    (CAREERS, CAREERS.replace("https:", "http:")),
    (CAREERS, CAREERS.replace("careers.", "www.careers.")),
    (CAREERS, CAREERS.replace("careers.", "")),
    (CAREERS, CAREERS.replace(".org/", ".org:8443/")),
    (CAREERS, CAREERS + "-1"),
    (CAREERS + "?id=42", CAREERS + "?id=420"),
    (CAREERS + "?id=42", CAREERS),
    (CAREERS + "?id=", CAREERS),
    (CAREERS + "?q=a%20b", CAREERS + "?q=a+b"),  # a + kept as written
    ("https://ch.indeed.com/cmp/Example-Robotics?ref=a", "https://ch.indeed.com/cmp/Example-Robotics"),
    # Application systems: another id, and a parameter the rule does not list (it identifies).
    (GH_PAGE, GH_PAGE.replace("4012345", "4012346")),
    (GH_PAGE, GH_PAGE + "&gh_src=x"),
    (GH_BOARD, GH_BOARD.replace("4012345", "4012346")),
    (LEVER, LEVER.replace("000000000042", "000000000043")),
    (LEVER, LEVER + "?lever-source=board"),
    (WORKDAY, WORKDAY.replace("_R12345", "_R12346")),
    (WTTJ, WTTJ.replace("data-engineer_bern", "data-engineer_zurich")),
]  # fmt: skip


@pytest.mark.parametrize(("seen", "reported"), NOT_SAME)
def test_an_id_made_up_or_borrowed_and_the_near_misses_name_another_posting(seen, reported):
    assert key(seen), seen
    assert key(seen) != key(reported)


def test_the_tables_are_big_enough_and_cover_the_tracking_lists() -> None:
    assert len(SAME) >= 40
    assert len(NOT_SAME) >= 40
    for board in same_posting.BOARDS.values():  # each tracker of a board is in an accept row
        assert all(any(f"{name}=" in reported for _, reported in SAME) for name in board.tracking)


# A corpus of distinct postings, as boards and careers pages write them: never one key.
CORPUS = (
    [f"https://ch.indeed.com/rc/clk?jk={n:016x}&from=serp" for n in range(60)]
    + [f"https://www.linkedin.com/jobs/view/{4012345600 + n}/" for n in range(60)]
    + [f"https://careers.example.org/jobs/{n}" for n in range(30)]
    + [f"https://careers.example.org/jobs?id={n}" for n in range(30)]
    + [f"https://ats.example-hire.test/example-robotics/jobs/{n}?ref=board" for n in range(30)]
    + [f"https://jobs.example.test/offres/{246000 + n}/" for n in range(30)]
    + [GH_PAGE.replace("4012345", str(4012300 + n)) for n in range(20)]
    + [GH_BOARD.replace("4012345", str(4012400 + n)) for n in range(20)]
    + [LEVER.replace("000000000042", f"{n:012x}") for n in range(20)]
    + [WORKDAY.replace("_R12345", f"_R{12300 + n}") for n in range(20)]
    + [WTTJ.replace("data-engineer_bern", f"data-engineer-{n}_bern") for n in range(20)]
)


def test_distinct_postings_keep_distinct_keys() -> None:
    keys = [key(url) for url in CORPUS]
    assert all(keys)
    assert len(set(keys)) == len(CORPUS)


def test_the_store_and_the_session_read_one_rule() -> None:
    """The store's duplicate key (`slug.PostingKey`) is `url_key` of the session's key: two
    addresses the session names one posting are one in the store, as written and as the session
    keeps them (canonical), and the distinct postings stay distinct there."""
    for seen, reported in SAME:
        for a, b in ((seen, reported), (finder.canonical(seen), finder.canonical(reported))):
            assert slug.posting_url_key(a) == slug.posting_url_key(b), (seen, reported)
    stored = {slug.posting_url_key(url) for url in CORPUS}
    assert len(stored) == len(CORPUS)
    # A reader of the store's keys (LinkedIn's guest search, `_known_ids`) reads a board's id.
    for url in CORPUS:
        named = same_posting.posting_of_key(slug.posting_url_key(url) or "")
        assert named == same_posting.posting_id(finder.canonical(url)), url


@pytest.mark.parametrize(
    ("url", "found"),
    [
        (f"https://ch.indeed.com/rc/clk?jk={JK}&from=serp", ("indeed.com", JK)),
        (f"http://ch.indeed.test:8080/viewjob?jk={JK}", ("indeed.test", JK)),
        (f"https://www.linkedin.com/jobs/view/{LI}/", ("linkedin.com", LI)),
        (f"https://www.linkedin.com/jobs/search/?currentJobId={LI}", ("linkedin.com", LI)),
        (f"http://www.linkedin.test:8080/jobs/view/x-{LI}", ("linkedin.test", LI)),
        (f"https://ch.indeed.com/viewjob?jk={JK}&jk={JK2}", None),
        (f"https://ch.indeed.com/viewjob?jk={JK[:8]}", None),
        (f"https://www.welcometothejungle.com/fr/companies/x/jobs/{LI}", None),  # no ids: generic
        (f"https://careers.example.org/viewjob?jk={JK}", None),
    ],
)
def test_the_posting_id_of_an_address(url: str, found) -> None:
    assert finder.posting_id(finder.canonical(url)) == found


@pytest.mark.parametrize(
    ("url", "address"),
    [
        (f"https://ch.indeed.com/rc/clk?jk={JK}&from=serp", f"https://ch.indeed.com/viewjob?jk={JK}"),
        (f"http://fr.indeed.test:8080/m/x?jk={JK}", f"http://fr.indeed.test:8080/viewjob?jk={JK}"),
        (f"https://ch.linkedin.com/jobs/view/a-b-{LI}?position=1",
         f"https://ch.linkedin.com/jobs/view/{LI}/"),
        (CAREERS + "?utm_source=x", CAREERS + "?utm_source=x"),  # no board: itself
    ],
)  # fmt: skip
def test_the_boards_own_address_of_a_posting(url: str, address: str) -> None:
    assert finder.posting_address(finder.canonical(url)) == address


# --- item 2: a script's item giving a posting's id ----------------------------------------------

INDEED_PAGE = "http://ch.indeed.test:8080/jobs?q=data&l=bern"
LINKEDIN_PAGE = "http://www.linkedin.test:8080/jobs/search/?keywords=data"
READ = "() => [...document.querySelectorAll('.job')].map(c => ({...c.dataset}))"
TITLE = {"title": "Data Engineer", "company": "Example Robotics"}


@pytest.mark.parametrize(
    ("page", "item", "code", "built"),
    [
        (INDEED_PAGE, {"jk": JK, **TITLE}, READ, f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
        (INDEED_PAGE, {"jobKey": JK, **TITLE}, READ, f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
        (INDEED_PAGE, {"job_key": JK, **TITLE}, READ, f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
        (INDEED_PAGE, {"JK": JK, **TITLE}, READ, f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
        (LINKEDIN_PAGE, {"jobId": LI, **TITLE}, READ, f"http://www.linkedin.test:8080/jobs/view/{LI}/"),
        (LINKEDIN_PAGE, {"jobId": int(LI), **TITLE}, READ,
         f"http://www.linkedin.test:8080/jobs/view/{LI}/"),
        (LINKEDIN_PAGE, {"entityUrn": f"urn:li:fsd_jobPosting:{LI}", **TITLE}, READ,
         f"http://www.linkedin.test:8080/jobs/view/{LI}/"),
        (LINKEDIN_PAGE, {"job-posting-id": LI, **TITLE}, READ,
         f"http://www.linkedin.test:8080/jobs/view/{LI}/"),
        # One id given twice, in two fields, is one card; two different ids are none.
        (INDEED_PAGE, {"jk": JK, "jobKey": JK, **TITLE}, READ,
         f"http://ch.indeed.test:8080/viewjob?jk={JK}"),
        (LINKEDIN_PAGE, {"jobId": LI, "entityUrn": f"urn:li:jobPosting:{LI}", **TITLE}, READ,
         f"http://www.linkedin.test:8080/jobs/view/{LI}/"),
        (INDEED_PAGE, {"jk": JK, "jobKey": JK2, **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"jobId": LI, "entityUrn": "urn:li:jobPosting:4012345679", **TITLE}, READ,
         None),
        (LINKEDIN_PAGE, {"jobId": LI, "currentJobId": 4012345679, **TITLE}, READ, None),
        # Refused: a field the board does not name, an id of another shape or another board's
        # field, an id the script's code writes, a page of no board, an item with no words.
        (INDEED_PAGE, {"id": JK, **TITLE}, READ, None),
        (INDEED_PAGE, {"jk": JK[:-1], **TITLE}, READ, None),
        (INDEED_PAGE, {"jk": [JK], **TITLE}, READ, None),
        (INDEED_PAGE, {"jobId": LI, **TITLE}, READ, None),
        (INDEED_PAGE, {"jk": JK, **TITLE}, f"() => [{{jk: '{JK}', title: document.title}}]", None),
        (LINKEDIN_PAGE, {"jk": JK, **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"jobId": True, **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"jobId": -4012345678, **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"jobId": f"{LI}x", **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"entityUrn": f"urn:li:company:{LI}", **TITLE}, READ, None),
        (LINKEDIN_PAGE, {"jobId": LI, **TITLE}, f"() => 'jobs/view/' + {LI}", None),
        ("https://jobs.example.test/search?q=data", {"jk": JK, **TITLE}, READ, None),
        (INDEED_PAGE, {"jk": JK}, READ, None),
    ],
)  # fmt: skip
def test_a_script_item_giving_a_posting_id_is_its_card(page, item, code, built) -> None:
    cards = finder._script_cards(page, json.dumps([item]), code)
    assert [link for link, _, _ in cards] == ([built] if built else [])
    for _, text, _ in cards:  # the card's words, never the id it gave
        assert text == "Data Engineer\nExample Robotics"


def test_an_item_with_a_link_and_an_id_is_the_card_of_both() -> None:
    item = {"link": f"/rc/clk?jk={JK}&from=serp", "jk": JK, **TITLE}
    links = [link for link, _, _ in finder._script_cards(INDEED_PAGE, json.dumps([item]), READ)]
    assert links == [
        f"http://ch.indeed.test:8080/rc/clk?jk={JK}&from=serp",
        f"http://ch.indeed.test:8080/viewjob?jk={JK}",
    ]
    assert len({key(link) for link in links}) == 1


# --- the session: what it saw, and a report of another spelling ---------------------------------

INDEED = "http://ch.indeed.test:8080/"
LINKEDIN = "http://www.linkedin.test:8080/"
FIRST = '- searchbox "What" [ref=e1]\n- button "Find jobs" [ref=e2]'


class NoTools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"no browser call expected: {name}")


def board_session(start: str, *reads: tuple[str, str]):
    """A website's session on `start` that read each (address, snapshot); the app's open of a
    page (`_open_for_report`) reads `served` and records each address it was asked for."""
    pages = sources.Pages(
        NoTools(), 1000, sleep=lambda _: asyncio.sleep(0), rng=random.Random(3),  # noqa: S311  deterministic test pacing
        monotonic=lambda: 0,
    )  # fmt: skip
    one = finder.FinderSession(
        pages, start, FIRST, "e2e-person",
        interactive=finder.InteractiveScope(start, ("data",), (), ()),
    )  # fmt: skip
    one.clock = lambda: datetime(2026, 11, 27, 8, 30, tzinfo=UTC)
    batches: list[finder.VerifiedBatch] = []
    opened: list[str] = []
    served: dict[str, str] = {}

    async def commit(batch: finder.VerifiedBatch) -> None:
        batches.append(batch)

    async def opens(url: str) -> str:
        opened.append(url)
        if url not in served:
            return ""
        one._read(url, served[url])
        one.opened_by_app += 1
        return url

    one.on_verified = commit
    one._open_for_report = opens  # type: ignore[method-assign]
    for url, snapshot in reads:
        one._read(url, snapshot)
    return one, batches, opened, served


def reported(one: finder.FinderSession, *items: dict[str, Any]) -> dict[str, Any]:
    answer, _ = asyncio.run(one.report_verified(list(items)))
    return json.loads(answer)


def listed(one: finder.FinderSession, *items: dict[str, Any]) -> dict[str, Any]:
    answer, _ = asyncio.run(one.report_listing(list(items)))
    return json.loads(answer)


def posting(title: str) -> str:
    return dressed(f'- heading "{title}" [level=1]\n- paragraph: Build invented robots in Bern.')


def indeed_list(keys: list[str], page: int = 1) -> tuple[str, str]:
    rows = ['- heading "Data jobs in Bern" [level=1]', "- list:"]
    for n, one in enumerate(keys):
        rows += [
            "  - listitem:",
            f'    - link "Data Role {one[-4:]}" [ref=e{n + 9}]:',
            f"      - /url: /rc/clk?jk={one}&bb=x{n}&xkcb=y&vjs=3&tk=t{page}&from=serp",
        ]
    return INDEED + f"jobs?q=data&l=bern&start={(page - 1) * 10}", "\n".join(rows)


def item(one: str, url: str | None = None, title: str | None = None) -> dict[str, str]:
    return {
        "title": title or f"Data Role {one[-4:]}",
        "url": url or f"{INDEED}viewjob?jk={one}",
        "text": "A card.",
    }


def keys(count: int, start: int = 0) -> list[str]:
    return [f"{start + n:016x}" for n in range(count)]


def test_indeed_another_spelling_of_a_seen_link_is_opened_at_the_boards_own_address() -> None:
    """The run of 2026-11-27: the list links `/rc/clk?jk=<key>&...`, the agent reports
    `/viewjob?jk=<key>` (or another host of the family, another path): the app opens the board's
    own address of that key on the host it was seen on, never the agent's, and keeps it."""
    one, batches, opened, served = board_session(INDEED, indeed_list(keys(3)))
    for k in keys(3):
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    answer = reported(
        one,
        item(keys(3)[0]),
        item(keys(3)[1], f"http://www.indeed.test:8080/m/basecamp/viewjob?jk={keys(3)[1]}&x=1"),
        item(keys(3)[2], f"/viewjob?jk={keys(3)[2]}&from=serp&utm_source=agent"),
    )
    assert (answer["kept"], answer["refused"]) == (3, [])
    assert opened == [f"{INDEED}viewjob?jk={k}" for k in keys(3)]
    assert [f.url for f in batches[0].findings] == opened
    assert all(f.list_url is None for f in batches[0].findings)  # read, never a card


def test_a_seen_address_reported_as_it_was_seen_is_opened_as_it_was() -> None:
    one, _, opened, served = board_session(INDEED, indeed_list(keys(1)))
    seen = f"{INDEED}rc/clk?jk={keys(1)[0]}&bb=x0&xkcb=y&vjs=3&tk=t1&from=serp"
    served[seen] = posting("Data Role 0000")
    assert reported(one, item(keys(1)[0], seen))["kept"] == 1
    assert opened == [seen]


@pytest.mark.parametrize(
    ("why", "reads", "url"),
    [
        ("an id never seen", [indeed_list(keys(2))], f"{INDEED}viewjob?jk={JK}"),
        ("a piece of a seen id", [indeed_list([JK])], f"{INDEED}viewjob?jk={JK[:-1]}"),
        ("a seen id and one more digit", [indeed_list([JK])], f"{INDEED}viewjob?jk={JK}0"),
        (
            "an id seen on another host family",
            [("https://jobs.example.test/mirror", f'- link "Data Role 6c7d":\n'
              f"  - /url: /apply?jk={JK}")],
            f"{INDEED}viewjob?jk={JK}",
        ),
        (
            "an id under another name",
            [(INDEED + "jobs?q=data", f'- link "Data Role 6c7d":\n  - /url: /jobs?q=d&vjk={JK}')],
            f"{INDEED}viewjob?jk={JK}",
        ),
        (
            "a tracking parameter that identifies on another board",
            [("https://jobs.example.test/list", '- link "Data Role 6c7d":\n'
              "  - /url: /offer?tk=111")],
            "https://jobs.example.test/offer?tk=222",
        ),
        ("only the report names it", [], f"{INDEED}viewjob?jk={JK}"),
    ],
)  # fmt: skip
def test_an_id_the_session_never_saw_stays_refused(why: str, reads, url: str) -> None:
    one, batches, opened, _ = board_session(INDEED, *reads)
    answer = reported(one, item(JK, url, "Data Role 6c7d"))
    assert answer["refused"] == [
        {"index": 0, "reason": "not_seen", "do": finder.REPORT_ADVICE["not_seen"]}
    ], why
    assert (opened, batches) == ([], [])
    assert listed(one, {"title": "Data Role 6c7d", "url": url})["refused"][0]["reason"] == (
        "card_not_seen"
    )


def test_an_id_only_the_scripts_own_code_writes_stays_refused() -> None:
    one, batches, opened, _ = board_session(INDEED)
    code = f"() => [{{jk: '{JK}', title: document.querySelector('h2').innerText}}]"
    asyncio.run(one._keep_script(INDEED_PAGE, json.dumps([{"jk": JK, **TITLE}]), False, code))
    assert reported(one, item(JK, title="Data Engineer"))["refused"][0]["reason"] == "not_seen"
    assert (opened, batches) == ([], [])


def test_a_borrowed_tracking_value_opens_the_seen_address_never_the_reported_one() -> None:
    """On Indeed's family `tk` only tracks: an address that differs from a seen one by it names
    the same page, and the app opens the address the session saw, so what it keeps is what the
    session saw."""
    seen = f"{INDEED}cmp/Example-Robotics/jobs/data-engineer?tk=111&from=serp"
    one, batches, opened, served = board_session(
        INDEED, (INDEED + "companies", f'- link "Data Engineer":\n  - /url: {seen}')
    )
    served[seen] = posting("Data Engineer")
    url = f"{INDEED}cmp/Example-Robotics/jobs/data-engineer?tk=222"
    assert reported(one, item("x", url, "Data Engineer"))["kept"] == 1
    assert opened == [seen]
    assert batches[0].findings[0].url == seen


def test_a_script_id_alone_counts_as_seen_and_its_item_is_its_card() -> None:
    """Item 2: the script gives `jk` and a title, no link. The address the agent builds from it
    is opened by the app; past the opens of a call, the item is its card."""
    one, batches, opened, served = board_session(INDEED)
    result = [{"jk": k, "title": f"Data Role {k[-4:]}", "company": "Example Robotics"}
              for k in keys(3)]  # fmt: skip
    asyncio.run(one._keep_script(INDEED_PAGE, json.dumps(result), False, READ))
    served[f"{INDEED}viewjob?jk={keys(3)[0]}"] = posting("Data Role 0000")
    answer = reported(one, *({**item(k), "company": "Example Robotics"} for k in keys(3)))
    assert answer["kept"] == 3
    assert opened == [f"{INDEED}viewjob?jk={k}" for k in keys(3)]
    findings = [f for batch in batches for f in batch.findings]
    assert [f.list_url for f in findings] == [None, INDEED_PAGE, INDEED_PAGE]
    assert findings[1].text == "Data Role 0001\nExample Robotics"
    assert findings[1].company == "Example Robotics"
    assert answer["from_card"] == [{"indexes": [1, 2], "why": finder.CARD_WHY["detail_not_opened"]}]


def test_twenty_opens_then_cards_in_the_same_call() -> None:
    """Item 3: 30 postings seen as cards: the app opens 20, keeps the other 10 from their card
    in the same call, and the answer says which and why; nothing is refused."""
    one, batches, opened, served = board_session(INDEED, indeed_list(keys(30)))
    for k in keys(30):
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    answer = reported(one, *(item(k) for k in keys(30)))
    assert (answer["kept"], answer["refused"], answer["kept_from_card"]) == (30, [], 10)
    assert answer["from_card"] == [
        {"indexes": list(range(20, 30)), "why": finder.CARD_WHY["app_opens_max"]}
    ]
    assert len(opened) == finder.APP_OPENS_MAX == one.opened_by_app
    findings = [f for batch in batches for f in batch.findings]
    assert [f.list_url is None for f in findings] == [True] * 20 + [False] * 10
    assert one.refused == {}
    # Reported again, every one is a duplicate: none is opened again.
    again = reported(one, *(item(k) for k in keys(30)))
    assert [r["reason"] for r in again["refused"]] == ["duplicate"] * 30
    assert len(opened) == finder.APP_OPENS_MAX


def test_the_opens_go_first_to_postings_no_card_stands_in_for() -> None:
    """25 cards then 10 addresses seen with no card (a link with no words): the app opens the 10
    and 10 of the cards, keeps the other 15 cards from their card: nothing is refused."""
    bare = keys(10, 100)
    rows = "\n".join(f"- link [ref=e{n}]:\n  - /url: /rc/clk?jk={k}" for n, k in enumerate(bare))
    one, _, opened, served = board_session(
        INDEED, indeed_list(keys(25)), (INDEED + "jobs?q=data&start=90", rows)
    )
    for k in keys(25) + bare:
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    answer = reported(one, *(item(k) for k in keys(25) + bare))
    assert (answer["kept"], answer["refused"], answer["kept_from_card"]) == (35, [], 15)
    assert opened == [f"{INDEED}viewjob?jk={k}" for k in keys(10) + bare]
    assert answer["from_card"] == [
        {"indexes": list(range(10, 25)), "why": finder.CARD_WHY["app_opens_max"]}
    ]


def test_past_the_opens_a_posting_with_no_card_is_refused_with_what_to_do() -> None:
    bare = keys(21, 100)
    rows = "\n".join(f"- link [ref=e{n}]:\n  - /url: /rc/clk?jk={k}" for n, k in enumerate(bare))
    one, _, _, served = board_session(INDEED, (INDEED + "jobs?q=data", rows))
    for k in bare:
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    answer = reported(one, *(item(k) for k in bare))
    assert answer["kept"] == finder.APP_OPENS_MAX
    assert answer["refused"] == [
        {"index": 20, "reason": "app_opens_max", "do": finder.REPORT_ADVICE["app_opens_max"]}
    ]
    assert "next report" in finder.REPORT_ADVICE["app_opens_max"]
    assert one.refused == {"app_opens_max": 1}


def bare_links(*bare: str) -> tuple[str, str]:
    """A page of links to postings with no words of their own: no card stands in for them."""
    rows = "\n".join(f"- link [ref=e{n}]:\n  - /url: /rc/clk?jk={k}" for n, k in enumerate(bare))
    return INDEED + "jobs?q=data&start=90", rows


def served_list(count: int) -> tuple[finder.FinderSession, list, list[str]]:
    """A session that read a list of `count` cards, every posting's page served."""
    one, batches, opened, served = board_session(INDEED, indeed_list(keys(count)))
    for k in keys(count):
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    return one, batches, opened


def test_a_posting_an_earlier_report_kept_takes_no_open_from_the_cards_before_it() -> None:
    """Review of #854, round 1: 25 postings reported, 20 opened and 5 kept from their card; then
    20 new ones with a card and those 5 again. The 5 are refused `duplicate` and take no open, so
    the 20 new ones are all opened, none kept from its card."""
    one, _, opened = served_list(45)
    first = reported(one, *(item(k) for k in keys(25)))
    assert (first["kept"], first["kept_from_card"]) == (25, 5)
    second = reported(one, *(item(k) for k in keys(20, 25)), *(item(k) for k in keys(5, 20)))
    assert second["kept"] == finder.APP_OPENS_MAX
    assert "from_card" not in second
    assert [r["reason"] for r in second["refused"]] == ["duplicate"] * 5
    assert len(opened) == 2 * finder.APP_OPENS_MAX


def test_an_item_refused_before_any_open_takes_no_open_from_the_cards_before_it() -> None:
    """Review of #854, round 1: 20 postings with a card, then 5 items with an empty title."""
    one, _, opened = served_list(25)
    answer = reported(one, *(item(k) for k in keys(20)), *(item(k, title=" ") for k in keys(5, 20)))
    assert answer["kept"] == finder.APP_OPENS_MAX
    assert "from_card" not in answer
    assert [r["reason"] for r in answer["refused"]] == ["missing_field"] * 5
    assert len(opened) == finder.APP_OPENS_MAX


def test_a_posting_named_twice_in_one_report_takes_one_open() -> None:
    """15 postings with a card, 5 with none, then the same 5 under another spelling: the 5 take
    5 opens, not 10, so the 15 with a card are opened too."""
    bare = keys(5, 100)
    one, _, opened, served = board_session(INDEED, indeed_list(keys(15)), bare_links(*bare))
    for k in keys(15) + bare:
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    again = [item(k, f"http://fr.indeed.test:8080/m/viewjob?jk={k}") for k in bare]
    answer = reported(one, *(item(k) for k in keys(15) + bare), *again)
    assert answer["kept"] == finder.APP_OPENS_MAX
    assert "from_card" not in answer
    assert [r["reason"] for r in answer["refused"]] == ["duplicate"] * 5
    assert opened == [f"{INDEED}viewjob?jk={k}" for k in keys(15) + bare]


def test_a_page_that_did_not_load_is_opened_once_a_report() -> None:
    """A posting with no card reported twice, its page not loading: one open, both refused."""
    k = keys(1, 100)[0]
    one, batches, opened, _ = board_session(INDEED, bare_links(k))
    answer = reported(one, item(k), item(k, f"{INDEED}rc/clk?jk={k}"))
    assert opened == [f"{INDEED}viewjob?jk={k}"]
    assert [r["reason"] for r in answer["refused"]] == ["detail_not_opened"] * 2
    assert (answer["kept"], batches) == (0, [])


def test_the_opens_are_planned_again_after_each_open() -> None:
    """An open may change what the next postings need: the first posting's page moved to the
    last one's (a redirect), so the last needs no open, and the 19 postings with a card between
    them are all opened."""
    first, last = keys(2, 100)
    one, batches, opened, served = board_session(
        INDEED, indeed_list(keys(19)), bare_links(first, last)
    )
    for k in keys(19):
        served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    opens = one._open_for_report
    moved = f"{INDEED}viewjob?jk={last}&from=moved"

    async def redirected(url: str) -> str:
        if url != f"{INDEED}viewjob?jk={first}":
            return await opens(url)
        opened.append(url)
        one._read(moved, posting("Robot Welder"))
        return moved

    one._open_for_report = redirected  # type: ignore[method-assign]
    answer = reported(
        one, item(first), *(item(k) for k in keys(19)), item(last, title="Robot Welder")
    )
    assert len(opened) == finder.APP_OPENS_MAX
    assert "from_card" not in answer
    assert answer["refused"] == [
        {
            "index": 0,
            "reason": "not_on_opened_page",
            "do": finder.REPORT_ADVICE["not_on_opened_page"],
        }
    ]
    assert [f.url for b in batches for f in b.findings][-1] == moved


def drawn_reports(seed: int) -> list[tuple[int, bool]]:
    """Four reports drawn at random (seeded) in one session, of postings with a card, postings
    with none, pages read, ids never seen, empty titles and repeats (of a posting an earlier
    report kept, or named earlier in the same one) under two spellings; one page in seven does
    not load. For each: the opens it used and whether a posting wanted one (kept from its card
    or refused `app_opens_max`). No posting is opened twice in one report."""
    rng = random.Random(seed)  # noqa: S311  a seeded draw, the same on every run
    carded, bare, read, unseen = keys(40), keys(30, 1000), keys(6, 2000), keys(6, 3000)
    rows = "\n".join(f"- link [ref=e{n}]:\n  - /url: /rc/clk?jk={k}" for n, k in enumerate(bare))
    one, _, opened, served = board_session(
        INDEED,
        indeed_list(carded),
        (INDEED + "jobs?q=data&start=90", rows),
        *((f"{INDEED}viewjob?jk={k}&from=serp", posting(f"Data Role {k[-4:]}")) for k in read),
    )
    for k in carded + bare:
        if int(k, 16) % 7:
            served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    pools = {"carded": carded, "bare": bare, "read": read, "unseen": unseen}
    named: list[str] = []
    results: list[tuple[int, bool]] = []
    for _ in range(4):
        items: list[dict[str, str]] = []
        for _ in range(rng.randint(10, 60)):
            kind = rng.choice([*pools, "carded", "bare", "again", "empty"])
            k = rng.choice(pools.get(kind) or named or carded)
            spelling = rng.choice([f"{INDEED}viewjob?jk={k}", f"{INDEED}rc/clk?jk={k}&tk=x"])
            items.append(item(k, spelling, " " if kind == "empty" else None))
            named.append(k)
        before, refused = len(opened), one.refused["app_opens_max"]
        answer = reported(one, *items)
        used = opened[before:]
        assert len({key(u) for u in used}) == len(used), seed
        wanting = one.refused["app_opens_max"] > refused or any(
            c["why"] == finder.CARD_WHY["app_opens_max"] for c in answer.get("from_card", [])
        )
        results.append((len(used), wanting))
    return results


def test_no_posting_is_kept_from_its_card_for_want_of_an_open_while_one_is_left() -> None:
    """The property of the plan (review of #854, round 1): whenever a posting is kept from its
    card or refused for want of an open, its report used all its APP_OPENS_MAX opens."""
    drawn = [one for seed in range(12) for one in drawn_reports(seed)]
    assert all(used <= finder.APP_OPENS_MAX for used, _ in drawn)
    assert all(used == finder.APP_OPENS_MAX for used, wanting in drawn if wanting)
    # Both cases were drawn, so the property was put to the test.
    assert {wanting for _, wanting in drawn} == {True, False}


def test_a_page_already_read_wins_over_its_card_and_over_an_open() -> None:
    k = keys(1)[0]
    page = f"{INDEED}viewjob?jk={k}&from=serp&vjs=3"
    one, batches, opened, _ = board_session(
        INDEED, indeed_list([k]), (page, posting(f"Data Role {k[-4:]}"))
    )
    answer = reported(one, item(k, f"{INDEED}rc/clk?jk={k}"))
    assert (answer["kept"], opened) == (1, [])
    [finding] = batches[0].findings
    assert (finding.url, finding.list_url, finding.evidence["rule"]) == (page, None, "heading")
    assert (
        listed(one, {"title": f"Data Role {k[-4:]}", "url": f"{INDEED}viewjob?jk={k}"})["refused"][
            0
        ]["reason"]
        == "duplicate"
    )


def test_two_spellings_in_one_report_are_one_posting() -> None:
    k = keys(1)[0]
    one, _, opened, served = board_session(INDEED, indeed_list([k]))
    served[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    answer = reported(one, item(k), item(k, f"{INDEED}rc/clk?jk={k}&from=serp"), item(k))
    assert answer["kept"] == 1
    assert [r["reason"] for r in answer["refused"]] == ["duplicate", "duplicate"]
    assert len(opened) == 1


def test_a_posting_kept_under_one_spelling_is_a_duplicate_under_another() -> None:
    """Kept from its card under the link as the list gives it (its page did not load), then
    reported as `/viewjob?jk=<key>`: one posting, refused `duplicate`, never opened again."""
    k = keys(1)[0]
    one, batches, opened, _ = board_session(INDEED, indeed_list([k]))
    seen = f"{INDEED}rc/clk?jk={k}&bb=x0&xkcb=y&vjs=3&tk=t1&from=serp"
    assert reported(one, item(k, seen))["kept"] == 1
    assert (opened, batches[0].findings[0].url) == ([seen], seen)
    for spelling in (f"{INDEED}viewjob?jk={k}", f"http://fr.indeed.test:8080/m/viewjob?jk={k}"):
        assert reported(one, item(k, spelling))["refused"][0]["reason"] == "duplicate"
        assert (
            listed(one, {"title": f"Data Role {k[-4:]}", "url": spelling})["refused"][0]["reason"]
            == "duplicate"
        )
    assert opened == [seen]


def test_a_gone_page_under_another_spelling_refuses_the_card() -> None:
    k = keys(1)[0]
    one, _, _, _ = board_session(INDEED, indeed_list([k]))
    one.error_pages[f"{INDEED}viewjob?jk={k}&from=serp"] = 404
    one._note_seen([f"{INDEED}viewjob?jk={k}&from=serp"])
    answer = listed(one, {"title": f"Data Role {k[-4:]}", "url": f"{INDEED}viewjob?jk={k}"})
    assert answer["refused"][0]["reason"] == "page_error"
    one.error_pages[f"{INDEED}viewjob?jk={k}&from=serp"] = 403  # walled: the card stands in
    assert (
        listed(one, {"title": f"Data Role {k[-4:]}", "url": f"{INDEED}viewjob?jk={k}"})["kept"] == 1
    )


def test_linkedin_a_public_job_link_with_its_position_is_a_card() -> None:
    """LinkedIn's public result cards link `/jobs/view/<slug>-<id>?position=1&pageNum=0&...`:
    `position` reads as a search parameter (`route_of`), yet a board's posting id names a
    posting. The agent reports `/jobs/view/<id>/`."""
    rows = ['- heading "Data jobs" [level=1]', "- list:"]
    for n in range(6):
        rows += [
            "  - listitem:",
            f'    - link "Data Role {n}" [ref=e{n}]:',
            f"      - /url: {LINKEDIN}jobs/view/data-role-{n}-at-example-{4012345600 + n}"
            f"?position={n + 1}&pageNum=0&refId=r{n}&trackingId=t{n}&trk=public_jobs",
        ]
    one, batches, _, _ = board_session(
        LINKEDIN, (LINKEDIN + "jobs/search?keywords=data", "\n".join(rows))
    )
    links = [
        f"{LINKEDIN}jobs/view/data-role-{n}-at-example-{4012345600 + n}?position={n + 1}"
        f"&pageNum=0&refId=r{n}&trackingId=t{n}&trk=public_jobs"
        for n in range(6)
    ]
    answer = listed(
        one,
        *({"title": f"Data Role {n}", "url": f"/jobs/view/{4012345600 + n}/"} for n in range(3)),
        *({"title": f"Data Role {n}", "url": links[n]} for n in range(3, 6)),  # as it was seen
    )
    assert (answer["kept"], answer["refused"]) == (6, [])
    assert [f.url for b in batches for f in b.findings] == [
        f"{LINKEDIN}jobs/view/{4012345600 + n}/" for n in range(3)
    ] + links[3:]
    assert finder.route_of(links[3], {LINKEDIN}, [], typed_address=True) == "search_address"


def search_with_two_cards(url: str, links: tuple[str, str]) -> tuple[str, str]:
    """A search page (fewer than LIST_CARDS_MIN cards, so no list) whose address holds a
    posting's id, its heading sharing a word with the posting's title."""
    return url, "\n".join(
        [
            '- heading "Data jobs" [level=1]',
            '- link "Data Engineer" [ref=e1]:',
            f"  - /url: {links[0]}",
            '- link "Robot Welder" [ref=e2]:',
            f"  - /url: {links[1]}",
            "- paragraph: Data Engineer, Example Robotics, Bern.",
        ]
    )


@pytest.mark.parametrize(
    ("start", "search", "links", "own"),
    [
        (LINKEDIN, f"{LINKEDIN}jobs/search/?currentJobId={LI}&keywords=data",
         (f"/jobs/view/{LI}/", "/jobs/view/4012345679/"), f"{LINKEDIN}jobs/view/{LI}/"),
        (LINKEDIN, f"{LINKEDIN}jobs/collections/recommended/?currentJobId={LI}",
         (f"/jobs/view/{LI}/", "/jobs/view/4012345679/"), f"{LINKEDIN}jobs/view/{LI}/"),
        (INDEED, f"{INDEED}cmp/Example-Robotics/jobs?jk={JK}",
         (f"/rc/clk?jk={JK}", f"/rc/clk?jk={JK2}"), f"{INDEED}viewjob?jk={JK}"),
    ],
)  # fmt: skip
@pytest.mark.parametrize("title", ["Data Engineer", "Data Scientist"])
def test_a_search_page_holding_a_postings_id_is_never_its_page(start, search, links, own, title):
    """Review of #854, round 1: a search or company page read whose address holds the posting's
    id is not the posting's page. A report of the posting opens the board's own page and keeps
    it there with that page's title, never the search address with the list's heading."""
    one, batches, opened, served = board_session(start, search_with_two_cards(search, links))
    assert search not in one.lists
    served[own] = posting("Data Engineer")
    for url in (own, search):  # the posting's own address, and the search address as it is
        answer = reported(one, {"title": title, "url": url, "text": "A posting."})
        if url == own:
            assert answer["kept"] == 1
    assert opened == [own]
    [finding] = [f for batch in batches for f in batch.findings]
    assert (finding.url, finding.title, finding.list_url) == (own, "Data Engineer", None)


def test_a_search_address_holding_a_postings_id_is_kept_from_its_card_at_the_boards_own():
    """Review of #854, round 1: a list links its postings as search addresses that carry their
    id (`/jobs/search/?currentJobId=<id>&...`). Reported as they are, they are kept from their
    card at the board's own address of the posting, as J31 stores no search address; and the app
    opens that address, never the search."""
    rows = ['- heading "Data jobs" [level=1]', "- list:"]
    for n in range(6):
        rows += [
            "  - listitem:",
            f'    - link "Data Role {n}" [ref=e{n}]:',
            f"      - /url: /jobs/search/?currentJobId={4012345600 + n}&keywords=data&refId=r{n}",
        ]
    one, batches, opened, served = board_session(
        LINKEDIN, (LINKEDIN + "jobs/search/?keywords=data", "\n".join(rows))
    )
    searches = [
        f"{LINKEDIN}jobs/search/?currentJobId={4012345600 + n}&keywords=data&refId=r{n}"
        for n in range(6)
    ]
    answer = listed(one, *({"title": f"Data Role {n}", "url": searches[n]} for n in range(5)))
    assert (answer["kept"], answer["refused"]) == (5, [])
    served[f"{LINKEDIN}jobs/view/4012345605/"] = posting("Data Role 5")
    assert reported(one, {"title": "Data Role 5", "url": searches[5], "text": "A card."})["kept"]
    assert opened == [f"{LINKEDIN}jobs/view/4012345605/"]
    assert [f.url for b in batches for f in b.findings] == [
        f"{LINKEDIN}jobs/view/{4012345600 + n}/" for n in range(6)
    ]


def test_a_link_seen_past_the_seen_set_still_counts() -> None:
    """`seen` stops at SEEN_MAX links; what the session saw by posting is bounded on its own."""
    filler = "\n".join(f'- link "x{n}":\n  - /url: /filler/{n}' for n in range(finder.SEEN_MAX))
    one, _, opened, served = board_session(INDEED, (INDEED + "filler", filler), indeed_list([JK]))
    seen = f"{INDEED}rc/clk?jk={JK}&bb=x0&xkcb=y&vjs=3&tk=t1&from=serp"
    assert seen not in one.seen
    served[f"{INDEED}viewjob?jk={JK}"] = posting("Data Role 6c7d")
    assert reported(one, item(JK))["kept"] == 1
    assert opened == [f"{INDEED}viewjob?jk={JK}"]


def test_what_the_session_saw_by_posting_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(finder, "SEEN_POSTINGS_MAX", 3)
    one, _, _, _ = board_session(INDEED)
    one.postings.clear()
    one.postings_chars = 0
    one._note_seen([f"{CAREERS}?id={n}" for n in range(5)])
    assert [finder.canonical(a) for (a,) in one.postings.values()] == [
        f"{CAREERS}?id={n}" for n in (2, 3, 4)
    ]
    k = keys(1)[0]
    spellings = [f"{INDEED}rc/clk?jk={k}&from={n}" for n in range(6)]
    one._note_seen(spellings)
    assert one.postings[f"indeed.test {k}"] == tuple(spellings[-finder.SPELLINGS_MAX :])
    assert one._same(f"{INDEED}viewjob?jk={k}")[0] == spellings[-1]


def test_what_the_session_saw_is_bounded_by_characters_and_a_long_address_is_none(
    monkeypatch,
) -> None:
    one, _, _, _ = board_session(INDEED)
    monkeypatch.setattr(finder, "SEEN_CHARS_MAX", one.postings_chars + 300)
    one._note_seen([f"{CAREERS}?id={n}" for n in range(10)])
    assert one.postings_chars <= finder.SEEN_CHARS_MAX
    assert one.postings_chars == sum(finder._chars(k, a) for k, a in one.postings.items())
    assert f"{CAREERS}?id=9" in one._same(f"{CAREERS}?id=9&utm_source=x")
    assert one._same(f"{CAREERS}?id=0") == []  # the oldest dropped
    long = f"{CAREERS}?id={'7' * finder.ADDRESS_MAX}"
    one._note_seen([long])
    assert one._same(long) == []


def test_a_hostile_page_of_the_largest_size_stays_within_the_bounds() -> None:
    """A page of PAGE_MAX characters of distinct links, each near ADDRESS_MAX, then one of short
    links: what the session saw by posting stays within its bounds and its sum is exact."""
    long = [f'- link "x":\n  - /url: /j/{n}?q={"a" * 3_900}' for n in range(500)]
    short = [f'- link "x":\n  - /url: /rc/clk?jk={n:016x}&tk=t' for n in range(40_000)]
    one, _, _, _ = board_session(INDEED)
    for name, rows in (("long", long), ("short", short)):
        page = "\n".join(rows)[: finder.PAGE_MAX]
        asyncio.run(one._read_off_loop(INDEED + name, page))
    assert len(one.postings) <= finder.SEEN_POSTINGS_MAX
    assert one.postings_chars <= finder.SEEN_CHARS_MAX
    assert one.postings_chars == sum(finder._chars(k, a) for k, a in one.postings.items())
    assert all(
        len(a) <= finder.ADDRESS_MAX for spellings in one.postings.values() for a in spellings
    )


def test_the_postings_of_a_large_page_are_keyed_off_the_event_loop(monkeypatch) -> None:
    """The posting each link names is worked out with the page's parse, in a thread for a page
    past PARSE_INLINE_MAX (#797); only the page's own address is keyed in the loop."""
    on_loop: list[bool] = []
    keyed = finder.posting_key

    def watched(url: str) -> str:
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return keyed(url)

    monkeypatch.setattr(finder, "posting_key", watched)
    monkeypatch.setattr(finder, "PARSE_INLINE_MAX", 0)
    one, _, _, _ = board_session(INDEED)
    on_loop.clear()
    results, page = indeed_list(keys(30))
    asyncio.run(one._read_off_loop(results, page))
    assert on_loop.count(False) == 30  # the links, in the parse's thread
    assert on_loop.count(True) == 1  # the page's own address
    script = json.dumps([{"jk": k, "title": f"Data Role {k[-4:]}"} for k in keys(5, 50)])
    on_loop.clear()
    asyncio.run(one._keep_script(INDEED_PAGE, script, False, READ))
    assert on_loop
    assert True not in on_loop


def test_every_card_fallback_has_its_words_and_no_dash() -> None:
    assert set(finder.CARD_WHY) == finder.CARD_FALLBACK
    for said in [*finder.CARD_WHY.values(), finder.REPORT_ADVICE["app_opens_max"]]:
        assert " - " not in said
        assert "–" not in said
        assert "—" not in said
        assert "do not report them again" in said or "next report" in said


# --- the run of 2026-11-27, its shape replayed through the website's Runner ---------------------


def test_the_production_run_keeps_what_it_saw() -> None:
    """Through the Runner, browserd's tabs and the app's own opens (B27's harness): Indeed's
    result page read with its `/rc/clk?jk=<key>&...` links, then 25 postings reported as
    `/viewjob?jk=<key>` in one call (all refused `not_seen` before, and past 20 `app_opens_max`):
    20 opened, 5 kept from their card, none refused; the counters the sweep serves stay true."""
    from tests.browser.test_b20_agent import NoDatabasePolicy, run, selected, text_of, tool
    from tests.browser.test_b27_code_first import TabSite

    results, page = indeed_list(keys(25))
    pages = {INDEED: FIRST, results: page}
    for k in keys(25):
        pages[f"{INDEED}viewjob?jk={k}"] = posting(f"Data Role {k[-4:]}")
    site = TabSite(pages)
    seen: dict[str, Any] = {"kept": []}

    async def script(session: finder.FinderSession):
        async def keep(batch):
            seen["kept"].extend(batch.findings)

        session.on_verified = keep
        await tool(session, "browser_navigate", url=results)
        answer = await tool(session, "report_postings", postings=[item(k) for k in keys(25)])
        seen["report"] = json.loads(text_of(answer))
        return finder.Ran()

    policy = NoDatabasePolicy()  # the board's consent given, nothing stored
    entry, _, _ = run(selected(INDEED), site, script, policy=policy, sweep_id=str(uuid.uuid4()))
    report = seen["report"]
    assert (report["kept"], report["refused"], report["kept_from_card"]) == (25, [], 5)
    assert entry["opened_by_app"] == finder.APP_OPENS_MAX
    assert "not_seen" not in entry.get("refused", {})
    assert "app_opens_max" not in entry.get("refused", {})
    assert sum(f.list_url is not None for f in seen["kept"]) == 5  # the sweep's `listed`
    navigated = [a["url"] for n, a in site.calls if n == "browser_navigate"]
    assert [url for url in navigated if "viewjob" in url] == [
        f"{INDEED}viewjob?jk={k}" for k in keys(20)
    ]


def test_the_report_tool_says_the_cap_the_cards_past_it_and_the_ids() -> None:
    from app.browser import finder_tools

    said = finder_tools.DESCRIPTIONS["report_postings"]
    assert f"at most {finder.APP_OPENS_MAX} a report" in said
    assert "card of a list" in said
    assert "Indeed's jk, LinkedIn's job id" in said
    assert " - " not in said


@pytest.mark.parametrize(
    "url", ["", "mailto:e2e-person@example.org", "javascript:alert(1)", "ftp://x.example.org/j/1"]
)
def test_what_the_rule_cannot_read_has_no_key_and_is_never_noted(url: str) -> None:
    assert finder.posting_key(url) == ""
    one, _, _, _ = board_session(INDEED)
    before = dict(one.postings)
    one._note_seen([url])
    assert one.postings == before
