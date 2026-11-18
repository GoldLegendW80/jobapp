"""Jobs J21, acceptance 4 and 6: the recorded La Reunion run of "Anywhere on the web" through
the real sweep, runner and web source (`interactive.run_web`), a scripted model and a fake
browserd: no model, no search engine, no job site.

The script replays the Claude Code session of 2026-11-18 on the owner's Mac for La Reunion: web
searches written as a person writes them (French and English, by title and place, `site:`
queries of France Travail and of a niche board), then a web_fetch of a niche AI board's posting
and of a France Travail posting, both reported with their own addresses. The pages are invented
and served by the fake (`.test` hosts: nothing leaves the test)."""

import asyncio
import json
import random
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import finder
from app.browser.sources import MemoryPacer, Runner
from app.jobs import sweep
from app.jobs.models import Job, JobStatus, Sweep, SweepStatus
from tests.browser.fakes import FakeLeases, ScriptedFinder
from tests.browser.test_b20_agent import text_of, tool
from tests.jobs.sweep_helpers import jobs_of, new_sweep, now, read_sweep, search, set_profile

NICHE = "https://jobs.example-ai-board.test/offres/data-scientist-cdi-saint-denis-4821"
TRAVAIL = "https://candidat.francetravail.test/offres/recherche/detail/201XLKP"
# The searches of the Mac session (the agent's own words, as Claude Code's WebSearch got them).
QUERIES = (
    "data scientist CDI La Reunion",
    "ingenieur IA Saint-Denis Reunion offre emploi",
    "site:candidat.francetravail.fr data scientist Reunion",
    "machine learning engineer Reunion island job",
)


def fetched(url: str, title: str, *lines: str) -> dict[str, Any]:
    """browserd's web_fetch answer (tools/fetch.py: Playwright's snapshot grammar)."""
    body = "\n".join(f"- paragraph: {line}" for line in lines)
    snapshot = f'- heading "{title}" [level=1]\n{body}'
    return {"ok": True, "url": url, "status": 200, "title": title, "snapshot": snapshot}


PAGES = {
    NICHE: fetched(
        NICHE,
        "Data Scientist (CDI)",
        "Employer: Example Lagoon Analytics",
        "Location: Saint-Denis, La Reunion",
        "Build forecasting models in Python for an invented island utility.",
    ),
    TRAVAIL: fetched(
        TRAVAIL,
        "Ingenieur IA (H/F)",
        "Employer: Example Ocean Data",
        "Location: 974 - Saint-Pierre",
        "Contrat a duree indeterminee. Deploy language models for invented customers.",
    ),
}
POSTINGS = (
    {
        "title": "Data Scientist (CDI)",
        "company": "Example Lagoon Analytics",
        "location": "Saint-Denis, La Reunion",
        "url": NICHE,
        "text": "Build forecasting models in Python.",
    },
    {
        "title": "Ingenieur IA (H/F)",
        "company": "Example Ocean Data",
        "location": "Saint-Pierre, La Reunion",
        "url": TRAVAIL,
        "text": "Deploy language models.",
    },
)


class WebTools:
    """browserd for the web source's lease: web_fetch of the invented pages, nothing else."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        # The web scope's start, never opened (finder.WEB_START).
        assert args.pop("_discovery_scope") == {"start": finder.WEB_START}
        self.calls.append((name, args.get("url", "")))
        if name == "web_fetch" and args["url"] in PAGES:
            return PAGES[args["url"]]
        return {"ok": False, "error": "navigation_failed"}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": "about:blank", "snapshot": ""}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def searched_web(session: finder.FinderSession, query: str) -> str | None:
    """What the runner's gate does before each WebSearch (finder.web_search_hook)."""
    run_id = uuid.uuid4()
    with finder._LOCK:
        finder._SESSIONS[run_id] = session
    try:
        return finder.web_search_hook(run_id, {"query": query})
    finally:
        with finder._LOCK:
            finder._SESSIONS.pop(run_id, None)


async def la_reunion(session: finder.FinderSession) -> finder.Ran:
    assert session.interactive is not None
    assert session.interactive.web
    for query in QUERIES:
        assert searched_web(session, query) is None
    for posting in POSTINGS:
        answer = json.loads(text_of(await tool(session, "web_fetch", url=posting["url"])))
        assert answer["url"] == posting["url"]
    report = json.loads(text_of(await tool(session, "report_postings", postings=list(POSTINGS))))
    assert report["kept"] == 2, report
    return finder.Ran()


def profile(factory: sessionmaker[Session], user: uuid.UUID) -> None:
    set_profile(
        factory,
        user,
        search(
            title_include={"terms": ["data scientist", "ingenieur ia"], "advanced_regex": False},
            location={"terms": ["reunion", "saint-pierre"], "advanced_regex": False},
            interactive_sites=[],
            never_apply=[],
            web_search=True,
        ),
    )


def run(factory: sessionmaker[Session], user: uuid.UUID, tools: WebTools) -> Sweep:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    hook = Runner(
        FakeLeases(),  # type: ignore[arg-type]
        lambda *_: tools,
        sleep=sleep,
        rng=random.Random(5),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
        finder=ScriptedFinder(la_reunion),
    )
    sweep_id = new_sweep(factory, user)
    asyncio.run(sweep.run_sweep(factory, sweep_id, now, browser=hook))
    return read_sweep(factory, sweep_id)


def web_entry(row: Sweep) -> dict[str, Any]:
    [entry] = [s for s in row.stats["sources"] if s.get("mode") == "web"]
    return entry


def test_the_la_reunion_run_stores_the_niche_board_and_france_travail_postings(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    tools = WebTools()
    row = run(factory, alice, tools)
    assert row.status is SweepStatus.done
    entry = web_entry(row)
    assert entry["source_id"] == sweep.web_entry()["source_id"]
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 2, 0)
    assert entry["error"] is None
    stored = {job.url: job for job in jobs_of(factory, alice)}
    assert set(stored) == {NICHE, TRAVAIL}
    assert stored[NICHE].company == "Example Lagoon Analytics"
    assert stored[TRAVAIL].title == "Ingenieur IA (H/F)"
    # No start page was opened: the first browserd call is a fetch.
    assert [name for name, _ in tools.calls] == ["web_fetch", "web_fetch"]
    assert finder.WEB_START not in {url for _, url in tools.calls}


def test_a_posting_another_source_stored_is_a_duplicate_by_address_or_identity(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    with factory() as db, db.begin():
        # The France Travail posting at the same address, from an alert e-mail.
        db.add(
            Job(
                user_id=alice, slug="kept-travail", status=JobStatus.shortlisted,
                found_on=now().date(), company="Example Ocean Data", title="AI engineer",
                location="", url=TRAVAIL, source="alert:francetravail",
            )
        )  # fmt: skip
        # The niche board's posting as a company board listed it: same company, title, place.
        db.add(
            Job(
                user_id=alice, slug="kept-niche", status=JobStatus.found, found_on=now().date(),
                company="Example Lagoon Analytics", title="Data Scientist (CDI)",
                location="Saint-Denis, La Reunion",
                url="https://boards.example-lagoon.test/jobs/12", source="ashby:example-lagoon",
            )
        )  # fmt: skip
    row = run(factory, alice, WebTools())
    entry = web_entry(row)
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 0, 2)
    assert (row.stats["new"], row.stats["duplicates"]) == (0, 2)
    with factory() as db:
        count = db.scalar(sa.select(sa.func.count()).select_from(Job).where(Job.user_id == alice))
    assert count == 2
