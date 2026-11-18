"""Jobs J24 (#815): the search agents report what fits the profile, and the app keeps it.

The production sweep b95ff4f7 (2026-11-18, La Reunion) saw full stack and data engineer
postings and a data scientist posting asking 5 years, and reported none ("none is an AI
engineer post"), for a GenAI profile of about 3.5 years with full stack and data skills. The
owner (B21, 2026-11-04): "the agent must find jobs that fit with my profile, i don't want to find
exactly the title job I'v set". So the prompts of both agents say what fits (a saved title OR the
profile's skills or a role next to them, whatever the title wording, seniority or years asked),
and nothing in code refuses or drops a posting for its title not matching a saved title: the
verification (finder `_verified`), the website's and the web's sort (interactive `_Site`) and
the store (`sweep.commit_verified`) only drop unwanted titles and places and other contracts.
Scoring computes fit and gaps, as for every posting.

These run the real sweep, runner, sources and store with a scripted model and a fake browserd:
invented pages on `.test` hosts, nothing leaves the test."""

import asyncio
import json
import random
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.browser import finder, web_plan
from app.browser.sources import MemoryPacer, Runner
from app.jobs import sweep
from app.jobs.models import Job, JobStatus, Sweep, SweepStatus
from tests.browser.fakes import FakeLeases, ScriptedFinder
from tests.browser.test_b20_agent import text_of, tool
from tests.jobs.sweep_helpers import SITE, jobs_of, new_sweep, now, read_sweep, search, set_profile
from tests.jobs.test_j21_web_sweep import fetched

FULL_STACK = "https://jobs.example-lagoon.test/offres/developpeur-full-stack-4410"
DATA_ENGINEER = "https://candidat.francetravail.test/offres/recherche/detail/208QWER"
SENIOR = "https://jobs.example-ocean.test/careers/data-scientist-confirme-77"
ELSEWHERE = "https://jobs.example-capital.test/offres/data-engineer-paris-12"

# Saved: AI titles only, La Reunion, Paris excluded. None of the titles below holds a saved one.
TITLES = ["ingenieur ia", "genai engineer"]
PAGES = {
    FULL_STACK: fetched(
        FULL_STACK,
        "Developpeur Full Stack (H/F)",
        "Employer: Example Lagoon Software",
        "Location: Saint-Denis, La Reunion",
        "Build web applications in Python with FastAPI and React for an invented utility.",
    ),
    DATA_ENGINEER: fetched(
        DATA_ENGINEER,
        "Data Engineer",
        "Employer: Example Ocean Data",
        "Location: 974 Saint-Pierre, La Reunion",
        "Python pipelines and retrieval over invented island records.",
    ),
    SENIOR: fetched(
        SENIOR,
        "Data Scientist confirme",
        "Employer: Example Reef Analytics",
        "Location: Le Port, La Reunion",
        "5 years of experience required. Python, agentic tools and retrieval for invented data.",
    ),
    ELSEWHERE: fetched(
        ELSEWHERE,
        "Data Engineer",
        "Employer: Example Capital Data",
        "Location: Paris, France",
        "Python pipelines for an invented bank.",
    ),
}
POSTINGS = [
    {
        "title": PAGES[url]["title"],
        "company": "",
        "location": location,
        "url": url,
        "text": "As the page shows it.",
    }
    for url, location in (
        (FULL_STACK, "Saint-Denis, La Reunion"),
        (DATA_ENGINEER, "974 Saint-Pierre, La Reunion"),
        (SENIOR, "Le Port, La Reunion"),
        (ELSEWHERE, "Paris, France"),
    )
]
KEPT = {FULL_STACK, DATA_ENGINEER, SENIOR}


class Tools:
    """browserd: the website's start page and web_fetch of the invented pages, nothing else."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("_discovery", None)
        args.pop("_discovery_scope", None)
        self.calls.append((name, args.get("url", "")))
        if name == "web_fetch" and args.get("url") in PAGES:
            return PAGES[args["url"]]
        if name == "browser_navigate" and args.get("url") == SITE["url"]:
            return {"ok": True, "url": SITE["url"], "title": "Jobs", "snapshot": '- heading "Jobs"'}
        return {"ok": False, "error": "navigation_failed"}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": SITE["url"], "snapshot": '- heading "Jobs"'}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def profile(factory: sessionmaker[Session], user: uuid.UUID, *, web: bool) -> None:
    set_profile(
        factory,
        user,
        search(
            title_include={"terms": TITLES, "advanced_regex": False},
            title_exclude={"terms": ["sales"], "advanced_regex": False},
            location={"terms": ["reunion"], "advanced_regex": False},
            location_exclude={"terms": ["paris"], "advanced_regex": False},
            interactive_sites=[] if web else [SITE],
            never_apply=[],
            web_search=web,
        ),
    )


async def reports_everything(session: finder.FinderSession) -> finder.Ran:
    """The agent as J24's prompt asks: every posting it read is reported, whatever its title
    or the years it asks; the app judges the rest."""
    for posting in POSTINGS:
        await tool(session, "web_fetch", url=posting["url"])
    report = json.loads(text_of(await tool(session, "report_postings", postings=POSTINGS)))
    # Verification refuses none: no posting is refused for a title that is no saved title.
    assert report["kept"] == len(POSTINGS), report
    assert report["refused"] == [], report
    return finder.Ran()


def run(factory: sessionmaker[Session], user: uuid.UUID) -> Sweep:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    tools = Tools()
    hook = Runner(
        FakeLeases(),  # type: ignore[arg-type]
        lambda *_: tools,
        sleep=sleep,
        rng=random.Random(5),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
        finder=ScriptedFinder(reports_everything),
    )
    sweep_id = new_sweep(factory, user)
    asyncio.run(sweep.run_sweep(factory, sweep_id, now, browser=hook))
    return read_sweep(factory, sweep_id)


def stored(factory: sessionmaker[Session], user: uuid.UUID) -> dict[str, Job]:
    return {job.url: job for job in jobs_of(factory, user)}


def assert_kept_what_fits(jobs: dict[str, Job]) -> None:
    # Full stack and data engineer postings, AI titles saved: stored, scored, found.
    assert set(jobs) == KEPT
    assert jobs[FULL_STACK].title == "Developpeur Full Stack (H/F)"
    assert jobs[DATA_ENGINEER].title == "Data Engineer"
    for job in jobs.values():
        assert job.status is JobStatus.found
        assert job.fit is not None
    # 5 years asked of a 3.5 years profile: stored, the gap shown by scoring.
    assert "5+ years" in jobs[SENIOR].gaps
    assert "5+ years" not in jobs[FULL_STACK].gaps


def test_the_web_source_stores_what_fits_the_profile_not_only_the_saved_titles(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice, web=True)
    row = run(factory, alice)
    assert row.status is SweepStatus.done
    [entry] = [s for s in row.stats["sources"] if s.get("mode") == "web"]
    assert entry["error"] is None
    assert entry["postings"] == len(KEPT)
    # The posting in another place (Paris, excluded) is never stored.
    assert entry["unwanted"] == 1
    assert_kept_what_fits(stored(factory, alice))


def test_a_website_stores_what_fits_the_profile_not_only_the_saved_titles(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice, web=False)
    row = run(factory, alice)
    assert row.status is SweepStatus.done
    [entry] = [s for s in row.stats["sources"] if s.get("host") == "jobs.example-robotics.test"]
    assert entry["postings"] == len(KEPT)
    assert entry["unwanted"] == 1
    assert_kept_what_fits(stored(factory, alice))


def test_both_prompts_report_what_fits_whatever_the_title_seniority_or_years() -> None:
    for prompt in (finder.INTERACTIVE_PROMPT, finder.WEB_PROMPT):
        flat = " ".join(prompt.split())
        for words in (
            "OR the profile's skills or a role next to them",
            "whatever its title says, its seniority or the years of experience it asks",
            "Never leave a posting for the years of experience or the seniority it asks, its pay,"
            " or skills the profile has only in part",
            "When in doubt, report it",
            "Leave a posting only when it is in another place, closed or expired",
            "excluded by the person's own never apply rules, or no job posting at all",
            "France Travail",
        ):
            assert words in flat, (words, prompt[:40])
        assert not any(dash in prompt for dash in ("–", "—", " - "))
    web = " ".join(finder.WEB_PROMPT.split())
    assert "Indeed, Mitula" in web
    assert "search for the same job (its title and employer) on the employer's own site" in web
    # The plan the web agent reads says it too, row by row.
    assert "whatever its title wording" in web_plan.shown(())["report"]
