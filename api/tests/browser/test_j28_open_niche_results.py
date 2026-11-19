"""Jobs J28 (#823): the web source opens every niche AI board result in a wanted place.

Production acceptance of J27 (2026-11-19, sweep b9ca8a4b): the web agent searched its whole
plan, `site:the-intelligence-academy.com` queries naming La Réunion included, and never opened
one of that board's pages, where three Saint-Denis CDIs were online. Now code collects, from each
WebSearch answer, the results on a niche board (web_plan.NICHE) whose title or address names a
wanted place, or (round 1 of #824) whose address is a job posting of its board and whose query
names a wanted place, at most NICHE_LINKS_MAX a run, and report_no_postings is refused while one is
neither opened nor reported. Code opens nothing itself. Every address here is invented, served by
a fake browserd; no page of any board is ever requested."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.agents import fake_backend, runner
from app.agents.models import AgentEvent, RunStatus
from app.browser import finder, web_plan
from app.browser.sources import interactive
from tests.browser.runtime_support import (  # noqa: F401  (fixtures)
    Runtime,
    alice,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runs_of,
    runtime,
)
from tests.browser.test_j23_web_breadth import (
    PLAN,
    PROFILE,
    TITLE,
    Browser,
    fetched,
    said,
    search,
    web_session,
)
from tests.browser.test_j27_finish_the_plan import APP, INIT, answers, result

# Two results of the AI board in Saint-Denis, La Réunion, and one in Paris (invented).
REUNION_1 = "https://the-intelligence-academy.com/job-board/ingenieur-ia-generative-example-lab"
REUNION_2 = "https://the-intelligence-academy.com/job-board/data-engineer-saint-denis-974"
PARIS = "https://the-intelligence-academy.com/job-board/data-scientist-paris-example"
LINKS = (
    ("Ingénieur IA générative (CDI), Example AI Lab, La Réunion", REUNION_1),
    ("Data engineer (CDI), Example Data", REUNION_2),
    ("Data scientist (CDI), Example Paris Lab, Paris", PARIS),
)
PAGE_1 = (
    '- heading "Ingénieur IA générative (CDI)" [level=1]\n'
    "- paragraph: Example AI Lab, Saint-Denis, La Réunion\n"
    "- paragraph: Agents, RAG and Python for an invented lab."
)
PAGE_2 = (
    '- heading "Data engineer (CDI)" [level=1]\n'
    "- paragraph: Example Data, Saint-Denis 97400\n"
    "- paragraph: Pipelines for an invented company."
)
POSTING_1 = {
    "title": "Ingénieur IA générative (CDI)",
    "url": REUNION_1,
    "text": "Agents, RAG and Python.",
    "location": "Saint-Denis, La Réunion",
}


def rows(*places: str) -> tuple[web_plan.Row, ...]:
    return web_plan.plan_of((TITLE,), places, "{}", PROFILE)


OVERSEAS = rows("La Réunion", "Guadeloupe", "Tahiti")
TIA = "https://the-intelligence-academy.com/job-board/"

# The accept and refuse table of web_plan.niche_link: (title, address, must be opened).
TABLE = [
    # The place in the title, as written, in capitals, without its article, by its number.
    ("Data scientist (CDI), Saint-Denis, La Réunion", TIA + "a1", True),
    ("INGÉNIEUR IA H/F LA REUNION", TIA + "a2", True),
    ("Développeur full stack, Réunion", TIA + "a3", True),
    ("Data engineer 974", TIA + "a4", True),
    ("Data engineer, Saint-Denis 97400", TIA + "a5", True),  # a shared town by its postcode
    ("Data analyst, 97410 Saint-Pierre", TIA + "a6", True),
    ("Data analyst, Saint-Denis 974", TIA + "a7", True),
    # The place in the address only, glued by hyphens or percent encoded.
    ("Ingénieur IA", TIA + "ingenieur-ia-la-reunion", True),
    ("Ingénieur IA", TIA + "ing%C3%A9nieur-ia-r%C3%A9union", True),
    ("Offre 4821", TIA + "offre?lieu=La%20R%C3%A9union", True),
    ("Data scientist", TIA + "data-scientist-974", True),
    # Other places of the plan, their towns no other place has, their other names.
    ("Data analyst, Pointe-à-Pitre", TIA + "b1", True),
    ("Développeur, Guadeloupe 971", TIA + "b2", True),
    ("Développeur, Papeete", TIA + "b3", True),
    ("Data engineer, Polynésie française", TIA + "b4", True),
    ("AI engineer, Tahiti", TIA + "b5", True),
    # Every board of the table, its www. host and a subdomain.
    ("Data scientist H/F La Réunion", "https://www.hellowork.com/fr-fr/emplois/1.html", True),
    ("Data scientist La Réunion", "https://hellowork.com/fr-fr/emplois/2.html", True),
    ("AI engineer, La Réunion", "https://www.welcometothejungle.com/fr/jobs/x", True),
    ("Data engineer Réunion", "https://www.free-work.com/fr/tech-it/jobs/y", True),
    ("Développeur Réunion", "https://www.lesjeudis.com/offre/z", True),
    ("Développeur Réunion", "https://www.chooseyourboss.com/offres/z", True),
    ("ML engineer, Reunion", "https://aijobs.net/job/1", True),
    ("ML engineer, Reunion", "https://ai-jobs.net/job/1", True),
    ("Data engineer La Réunion", "http://fr.the-intelligence-academy.com/job/1", True),
    # Another place, a shared town alone or by another postcode, a glued word.
    ("Data scientist (CDI), Paris", TIA + "c1", False),
    ("Data scientist, Saint-Denis", TIA + "c2", False),
    ("Data scientist, Saint-Denis 93200", TIA + "c3", False),
    ("Data scientist, Saint-Pierre", TIA + "c4", False),
    ("Data scientist, Martinique 972", TIA + "c5", False),
    ("Data scientist, Réunionnais bienvenus", TIA + "c6", False),
    ("Data scientist 97400123", TIA + "c7", False),
    ("Data scientist", TIA + "data-scientist-paris", False),
    ("", TIA, False),
    # No niche board: a national board, a look alike, a parent domain, another scheme.
    ("Data scientist La Réunion", "https://candidat.francetravail.fr/offres/1", False),
    ("Data scientist La Réunion", "https://the-intelligence-academy.com.example.test/x", False),
    ("Data scientist La Réunion", "https://notthe-intelligence-academy.com/x", False),
    ("Data scientist La Réunion", "https://academy.com/x", False),
    ("Data scientist La Réunion", "https://example-hellowork.com/x", False),
    ("Data scientist La Réunion", "ftp://the-intelligence-academy.com/x", False),
    ("Data scientist La Réunion", "the-intelligence-academy.com/x", False),
    ("Data scientist La Réunion", "", False),
    ("Data scientist La Réunion", "https://[::1/x", False),
    ("Data scientist La Réunion", TIA + "x" * 2000, False),
    # The place in the host only is no place: the host is the board's.
    ("Data scientist", "https://reunion.hellowork.com/emplois/1.html", False),
]


@pytest.mark.parametrize(("title", "url", "needed"), TABLE)
def test_which_results_must_be_opened(title: str, url: str, needed: bool) -> None:
    assert web_plan.niche_link(OVERSEAS, title, url) is needed


# Round 1 of #824: the real results of this exact query on 2026-11-19 named no place, neither in
# their titles nor in their addresses. Invented copies: the same titles and address shapes, the
# company slugs invented.
QUERY = (
    "site:the-intelligence-academy.com OR site:hellowork.com OR site:welcometothejungle.com"
    " OR site:free-work.com OR site:lesjeudis.com La Réunion data IA"
)
PARIS_QUERY = QUERY.replace("La Réunion", "Paris")
TIA_OFFRE = "https://www.the-intelligence-academy.com/job-board/offre/"
ENGINEER = TIA_OFFRE + "data-engineer-senior-ia-agentique-cdi-examplecorp-10508"
SCIENTIST = TIA_OFFRE + "data-scientist-cdi-exampleutility-25595"
HELLOWORK = "https://www.hellowork.com/fr-fr/emplois/81864557.html"
PLACELESS = (
    ("Programme IA sur mesureC'est gratuit →", ENGINEER),
    ("data scientist cdi exampleutility 25595", SCIENTIST),
)
TIA_WWW = "https://www.the-intelligence-academy.com"
HW_WWW = "https://www.hellowork.com"

# The accept and refuse table of a result read with the query that returned it:
# (title, address, query, must be opened).
QUERY_TABLE = [
    # The real results, under the real query, and a Hellowork posting.
    (*PLACELESS[0], QUERY, True),
    (*PLACELESS[1], QUERY, True),
    ("Data engineer H/F", HELLOWORK, QUERY, True),
    ("", ENGINEER, QUERY, True),
    # The place named otherwise by the query: capitals, no article, its number, a town of its
    # own, a shared town by its postcode, another place of the plan.
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data LA REUNION", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data Réunion", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data 974", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data Saint-Pierre 97410", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data Saint-Denis 97400", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data Pointe-à-Pitre", True),
    ("Offre", ENGINEER, "site:the-intelligence-academy.com data Tahiti", True),
    ("Offre", HELLOWORK, "site:hellowork.com data Guadeloupe", True),
    # The posting shape: a trailing slash, a query string, another locale, no www.
    ("Offre", ENGINEER + "/", QUERY, True),
    ("Offre", ENGINEER + "?utm_source=x", QUERY, True),
    ("Offre", "https://the-intelligence-academy.com/job-board/offre/x-1", QUERY, True),
    ("Offre", "https://hellowork.com/en-gb/emplois/1.html", QUERY, True),
    # Another place, no place, a shared town alone or by another postcode, a glued word, the
    # place only in an excluded word or a `site:` host.
    (*PLACELESS[0], PARIS_QUERY, False),
    (*PLACELESS[1], PARIS_QUERY, False),
    ("Offre", HELLOWORK, PARIS_QUERY, False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data IA", False),
    (*PLACELESS[0], "", False),
    (*PLACELESS[0], None, False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data Saint-Denis", False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data Saint-Denis 93200", False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data Saint-Pierre", False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data Martinique 972", False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data Réunionnais", False),
    (*PLACELESS[0], "site:the-intelligence-academy.com data IA -Réunion", False),
    (*PLACELESS[0], "site:reunion.example.com data IA", False),
    # A board page that is no posting, under the real query.
    ("Job board", TIA_WWW + "/job-board", QUERY, False),
    ("Job board", TIA_WWW + "/job-board/", QUERY, False),
    ("Job board", TIA_WWW + "/job-board/offre/", QUERY, False),
    ("Job board", TIA_WWW + "/job-board/offre", QUERY, False),
    ("Postuler", ENGINEER + "/candidature", QUERY, False),
    ("Formation", TIA_WWW + "/formations/ia-generative", QUERY, False),
    ("Programme IA sur mesureC'est gratuit →", TIA_WWW + "/", QUERY, False),
    ("Data scientist", "https://www.the-intelligence-academy.com/job-board/data-1", QUERY, False),
    ("Métier", HW_WWW + "/fr-fr/emploi/metier_data-scientist.html", QUERY, False),
    ("Emplois", HW_WWW + "/fr-fr/emplois.html", QUERY, False),
    ("Emplois", HW_WWW + "/fr-fr/emplois/data-scientist.html", QUERY, False),
    ("Emplois", HW_WWW + "/fr-fr/emplois/81864557", QUERY, False),
    ("Emplois", HW_WWW + "/emplois/81864557.html", QUERY, False),
    ("Emplois", HW_WWW + "/fr-fr/entreprises/example-81864557.html", QUERY, False),
    # Another niche board, its posting shape not checked (declared open point), a look alike
    # host, a national board.
    ("Offre", "https://www.welcometothejungle.com/fr/companies/example/jobs/data", QUERY, False),
    ("Offre", "https://www.free-work.com/fr/tech-it/data-scientist/job-mission/x", QUERY, False),
    ("Offre", "https://the-intelligence-academy.com.example.test/job-board/offre/x", QUERY, False),
    ("Offre", "https://example-hellowork.com/fr-fr/emplois/1.html", QUERY, False),
    ("Offre", "https://candidat.francetravail.fr/offres/recherche/detail/1", QUERY, False),
]


@pytest.mark.parametrize(("title", "url", "query", "needed"), QUERY_TABLE)
def test_which_results_must_be_opened_by_their_query(
    title: str, url: str, query: str | None, needed: bool
) -> None:
    assert web_plan.niche_link(OVERSEAS, title, url, query) is needed


def test_a_posting_needs_a_place_wanted_and_a_query() -> None:
    for title, url in PLACELESS:
        assert web_plan.niche_link(OVERSEAS, title, url) is False  # no query pending
        assert web_plan.niche_link(rows(), title, url, QUERY) is False  # no place wanted
        assert web_plan.niche_link(rows("Paris"), title, url, QUERY) is False
        assert web_plan.niche_link(rows("Paris"), title, url, PARIS_QUERY) is True
    assert web_plan.niche_link(OVERSEAS, *LINKS[2], QUERY) is False  # no posting, Paris
    assert web_plan.niche_link(OVERSEAS, *LINKS[0], PARIS_QUERY) is True  # its title


def test_a_plan_without_a_place_needs_no_result_and_bad_values_none() -> None:
    assert web_plan.niche_link(rows(), "Data scientist La Réunion", TIA + "x") is False
    assert web_plan.niche_link(rows("everywhere"), "Data scientist La Réunion", TIA) is False
    assert web_plan.niche_link(OVERSEAS, None, TIA + "la-reunion") is False  # type: ignore[arg-type]
    assert web_plan.niche_link(OVERSEAS, "La Réunion", None) is False  # type: ignore[arg-type]
    paris = rows("Paris")
    assert web_plan.niche_link(paris, *LINKS[2]) is True
    assert web_plan.niche_link(paris, *LINKS[0]) is False


def test_the_results_of_an_answer_are_read_in_order_and_bounded() -> None:
    line = json.dumps([{"title": t, "url": u} for t, u in LINKS])
    text = f'Web search results for query: "q"\n\nLinks: {line}\n\nA summary.\n'
    assert runner.web_search_links(text) == LINKS
    odd = 'Links: [{"title": 3, "url": "https://a.test/"}, {"url": 4}, "x", {"title": "y"}]\n'
    assert runner.web_search_links(odd) == (("", "https://a.test/"),)
    assert runner.web_search_links("Links: [not json]\n") == ()
    assert runner.web_search_links("No links found.") == ()
    many = json.dumps([{"title": "t", "url": f"https://a.test/{n}"} for n in range(300)])
    assert len(runner.web_search_links(f"Links: {many}\n")) == runner.WEB_LINKS_MAX


# --- the session ---------------------------------------------------------------------------------


def answer(
    session: finder.FinderSession,
    links: tuple[tuple[str, str], ...],
    failed: bool = False,
    query: str = "data La Réunion",
) -> None:
    """A WebSearch of `query` the gate let through, answered with `links` (the runner's
    word)."""
    run_id = uuid.uuid4()
    with finder._LOCK:
        finder._SESSIONS[run_id] = session
    try:
        call = f"toolu_{uuid.uuid4().hex}"
        assert finder.web_search_hook(run_id, {"query": query}, call) is None
        finder.WEB_FINDER.web_search_answered(run_id, failed, call, links)
    finally:
        with finder._LOCK:
            finder._SESSIONS.pop(run_id, None)


def browser() -> Browser:
    return Browser(
        {
            REUNION_1: fetched(REUNION_1, PAGE_1),
            REUNION_2: fetched(REUNION_2, PAGE_2),
            ENGINEER: fetched(ENGINEER, PAGE_1),
            SCIENTIST: fetched(SCIENTIST, PAGE_2),
        }
    )


def test_two_reunion_results_must_be_opened_before_the_run_finishes_not_the_paris_one() -> None:
    session = web_session(browser=browser())
    search(session, *PLAN)
    answer(session, LINKS)
    assert list(session.niche_links) == [REUNION_1, REUNION_2]
    said_, error = session.report_none("no_match")
    assert error
    assert said_.startswith("Refused: do not give up yet, the AI job board results")
    assert REUNION_1 in said_
    assert REUNION_2 in said_
    assert PARIS not in said_
    assert "Example Data" not in said_  # addresses only: a title is web text Activity keeps
    assert session.plan_unfinished()
    asyncio.run(session.web_fetch(REUNION_1))
    said_, error = session.report_none("no_match")
    assert error
    assert REUNION_1 not in said_
    assert REUNION_2 in said_
    # A report of the second, even one the verification refuses (never opened), is its judgment.
    reply, _ = session.report([{**POSTING_1, "url": REUNION_2, "title": "Data engineer (CDI)"}])
    assert "niche_results_left" not in json.loads(reply)
    said_, error = session.report_none("no_match")
    assert (error, session.finished, session.outcome) == (False, True, "no_match")
    assert session.go_on() is None
    assert not session.plan_unfinished()


def test_the_postings_of_a_reunion_query_must_be_opened_though_they_name_no_place() -> None:
    session = web_session(browser=browser())
    search(session, *PLAN)
    listing = ("Job board", TIA_WWW + "/job-board/")
    answer(session, (*PLACELESS, listing), query=QUERY)
    assert list(session.niche_links) == [ENGINEER, SCIENTIST]
    said_, error = session.report_none("no_match")
    assert error
    assert said_.startswith("Refused: do not give up yet, the AI job board results")
    assert ENGINEER in said_
    assert SCIENTIST in said_
    assert session.niche_left() == [ENGINEER, SCIENTIST]  # the listing page is no posting
    assert "Programme IA" not in said_  # addresses only
    asyncio.run(session.web_fetch(ENGINEER))
    said_, error = session.report_none("no_match")
    assert error
    assert ENGINEER not in said_
    assert SCIENTIST in said_
    reply, _ = session.report([{**POSTING_1, "url": SCIENTIST, "title": "Data scientist (CDI)"}])
    assert "niche_results_left" not in json.loads(reply)
    said_, error = session.report_none("no_match")
    assert (error, session.finished) == (False, True)


def test_the_same_postings_without_a_reunion_query_pending_are_not_kept() -> None:
    for query in (PARIS_QUERY, "site:the-intelligence-academy.com data IA"):
        session = web_session()
        answer(session, PLACELESS, query=query)
        assert session.niche_links == {}
    # No query pending: an answer with no call, or links noted alone, read title and address.
    session = web_session()
    session.web_search_answered(False, None, PLACELESS)
    session.note_niche_links(PLACELESS)
    session.web_search_answered(False, "toolu_unknown", PLACELESS)
    assert session.niche_links == {}
    session.web_search_answered(False, None, LINKS)
    assert list(session.niche_links) == [REUNION_1, REUNION_2]


def test_the_cap_holds_with_the_postings_of_a_query_first_in_answer_order() -> None:
    session = web_session()
    many = tuple(("Offre", f"{TIA_OFFRE}data-{n}") for n in range(20))
    answer(session, many[:2] + LINKS + many[2:], query=QUERY)
    expected = [many[0][1], many[1][1], REUNION_1, REUNION_2] + [u for _, u in many[2:13]]
    assert len(session.niche_links) == web_plan.NICHE_LINKS_MAX
    assert list(session.niche_links) == [finder.canonical(u) for u in expected]


def test_a_browser_navigate_opens_a_result_too() -> None:
    session = web_session(browser=browser())
    search(session, *PLAN)
    answer(session, LINKS[:1])
    asyncio.run(session.navigate(REUNION_1 + "#apply"))  # browserd fails it: it was opened
    assert session.niche_left() == []


def test_every_report_postings_answer_and_the_follow_up_name_the_results_left() -> None:
    session = web_session(browser=browser())
    search(session, *PLAN)
    answer(session, LINKS)
    asyncio.run(session.web_fetch(REUNION_1))
    reply = json.loads(session.report([POSTING_1])[0])
    assert REUNION_2 in reply["niche_results_left"]
    assert REUNION_1 not in reply["niche_results_left"]
    assert "plan_left" not in reply  # the plan is searched, only the result is left
    follow_up = session.go_on()
    assert follow_up is not None
    assert "not opened" in follow_up
    assert REUNION_2 in follow_up
    assert "not searched" not in follow_up


def test_the_plan_and_the_results_left_are_both_named() -> None:
    session = web_session(browser=browser())
    answer(session, LINKS)
    said_, error = session.report_none("no_match")
    assert error
    assert "not searched" in said_  # the plan first
    follow_up = session.go_on() or ""
    assert "not searched" in follow_up
    assert REUNION_1 in follow_up


def test_at_most_fifteen_results_a_run_in_answer_order() -> None:
    session = web_session()
    many = tuple((f"Data engineer {n}, La Réunion", f"{TIA}offre-{n}") for n in range(20))
    answer(session, many[:3] + LINKS[2:] + many[3:])
    assert web_plan.NICHE_LINKS_MAX == 15
    assert list(session.niche_links) == [url for _, url in many[:15]]
    answer(session, (("Data engineer, La Réunion", TIA + "late"),))
    assert TIA + "late" not in session.niche_links
    assert len(session.niche_left()) == 15


def test_a_result_twice_counts_once_and_one_opened_before_is_done() -> None:
    session = web_session(browser=browser())
    asyncio.run(session.web_fetch(REUNION_1))
    answer(session, LINKS)
    answer(session, LINKS)
    assert list(session.niche_links) == [REUNION_1, REUNION_2]
    assert session.niche_left() == [REUNION_2]


def test_a_failed_answer_and_a_website_run_collect_nothing() -> None:
    session = web_session()
    answer(session, LINKS, failed=True)
    assert session.niche_links == {}
    site = web_session()
    assert site.interactive is not None
    site.interactive = finder.InteractiveScope(
        finder.WEB_START, (TITLE,), ("La Réunion",), (), profile=PROFILE, web=False
    )
    site.note_niche_links(LINKS)
    assert site.niche_links == {}


@pytest.mark.parametrize("lift", ["stopped", "no_web_search", "failing"])
def test_what_lifts_the_plan_lifts_the_results_too(lift: str) -> None:
    session = web_session()
    search(session, *PLAN)
    answer(session, LINKS)
    assert session.niche_left()
    if lift == "stopped":
        session.stopped = "lease_ended"
    elif lift == "no_web_search":
        session.web_search_on = False
    else:
        search(session, "data La Réunion", "data La Réunion", failed=True)
    assert session.niche_left() == []
    assert session.report_none("no_match")[1] is False


# --- through the runner and the fake Claude Code --------------------------------------------------


def run_web(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
    steps: list[dict[str, Any]],
    links: tuple[tuple[str, str], ...] = LINKS,
) -> tuple[finder.FinderSession, list[str], list[tuple[str, Any]]]:
    """The stream replayed through the runner for a La Réunion web search, every WebSearch
    answered with `links`: the session, the addresses it stored, the run's events."""
    stream = {
        "description": "J28 test stream.",
        "web_search": [{"title": t, "url": u} for t, u in links],
        "steps": steps,
    }
    (tmp_path / "web_discovery.json").write_text(json.dumps(stream), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", tmp_path)
    runtime.give_key(alice)
    session = web_session(browser=browser())
    session.user_id = str(alice)
    stored: list[str] = []

    async def store(batch: finder.VerifiedBatch) -> None:
        stored.extend(f.url for f in batch.findings)

    session.on_verified = store
    asyncio.run(finder.RuntimeFinder(lambda: runtime.context())(session))
    [run] = runs_of(runtime.factory, alice)
    assert (run.agent, run.status) == (finder.WEB_NAME, RunStatus.done), run.error
    with runtime.factory() as database:
        events = [
            (event.kind, event.data)
            for event in database.scalars(
                sa.select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)
            )
        ]
    return session, stored, events


def test_the_runner_tells_the_results_and_the_give_up_waits_for_the_two_opened(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    steps = [
        INIT,
        said(1, *(("WebSearch", {"query": q}) for q in PLAN)),
        said(2, (APP + "report_no_postings", {"reason": "no_match"})),  # judged from the list
        said(3, (APP + "web_fetch", {"url": REUNION_1}), (APP + "web_fetch", {"url": REUNION_2})),
        said(4, (APP + "report_postings", {"postings": [POSTING_1]})),
        said(5, (APP + "report_no_postings", {"reason": "no_match"})),
        said(6),
        result(1),
    ]
    session, stored, events = run_web(runtime, alice, monkeypatch, tmp_path, steps)
    assert list(session.niche_links) == [REUNION_1, REUNION_2]
    refused, held = answers(events, "report_no_postings")
    assert refused.startswith("Refused: do not give up yet, the AI job board results")
    assert REUNION_1 in refused
    assert REUNION_2 in refused
    assert PARIS not in refused
    assert held == ""
    assert stored == [REUNION_1]
    assert "follow_up" not in [kind for kind, _ in events]
    assert (session.finished, session.niche_left()) == (True, [])
    assert interactive.outcome(session) is None


def test_the_runner_reads_the_postings_of_the_reunion_query_and_the_give_up_waits_for_them(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    steps = [
        INIT,
        said(1, *(("WebSearch", {"query": q}) for q in (*PLAN, QUERY))),
        said(2, (APP + "report_no_postings", {"reason": "no_match"})),  # judged from the list
        said(3, (APP + "web_fetch", {"url": ENGINEER})),
        said(4, (APP + "report_no_postings", {"reason": "no_match"})),
        said(5, (APP + "web_fetch", {"url": SCIENTIST})),
        said(6, (APP + "report_no_postings", {"reason": "no_match"})),
        said(7),
        result(1),
    ]
    listing = ("Job board", TIA_WWW + "/job-board/")
    session, _, events = run_web(
        runtime, alice, monkeypatch, tmp_path, steps, (*PLACELESS, listing)
    )
    assert list(session.niche_links) == [ENGINEER, SCIENTIST]
    first, second, held = answers(events, "report_no_postings")
    assert first.startswith("Refused: do not give up yet, the AI job board results")
    assert ENGINEER in first
    assert SCIENTIST in first
    assert "Programme IA" not in first
    assert ENGINEER not in second
    assert SCIENTIST in second
    assert held == ""
    assert (session.finished, session.niche_left()) == (True, [])
    assert interactive.outcome(session) is None


def test_a_run_that_opens_the_results_first_finishes_at_once(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    steps = [
        INIT,
        said(1, *(("WebSearch", {"query": q}) for q in PLAN)),
        said(2, (APP + "web_fetch", {"url": REUNION_1}), (APP + "web_fetch", {"url": REUNION_2})),
        said(3, (APP + "report_no_postings", {"reason": "no_match"})),
        said(4),
        result(1),
    ]
    session, _, events = run_web(runtime, alice, monkeypatch, tmp_path, steps)
    assert answers(events, "report_no_postings") == [""]
    assert "follow_up" not in [kind for kind, _ in events]
    assert session.finished
    assert interactive.outcome(session) is None


def test_a_run_that_never_opens_them_is_continued_then_ends_plan_unfinished(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    steps = [INIT, said(1, *(("WebSearch", {"query": q}) for q in PLAN)), said(2), result(1)]
    session, _, events = run_web(runtime, alice, monkeypatch, tmp_path, steps)
    assert session.web_left() == ""  # the plan is searched: only the results are left
    assert [data for kind, data in events if kind == "follow_up"] == [
        {"attempt": 1, "of": 2},
        {"attempt": 2, "of": 2},
    ]
    assert interactive.outcome(session) == "plan_unfinished"


def test_the_default_fake_web_stream_lists_no_niche_result() -> None:
    stream = json.loads((fake_backend.STREAMS / "web_discovery.json").read_text(encoding="utf-8"))
    assert stream["web_search"]
    for link in stream["web_search"]:
        assert web_plan.niche_board(link["url"]) is None
