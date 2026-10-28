"""Helpers of the sweep tests: a search section with one ticked job website, profiles, running a
sweep whose website "finds" the given postings (`websites`: a fake of the website Runner that
stores them through the run's commits, as the interactive finder does)."""

import asyncio
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.browser import finder
from app.jobs import sweep
from app.jobs.models import Job, Sweep, SweepTrigger
from app.profile.models import Profile
from tests.jobs.test_scoring import skills as make_skills

SKILLS = make_skills().model_dump(mode="json")


# A job website of the person's own, ticked: every search runs the websites alone (2026-10-28).
SITE = {
    "platform": "custom",
    "url": "https://jobs.example-robotics.test/",
    "enabled": True,
    "mode": "llm_browser",
}
# A second one, for what two websites do together (duplicates, source notes, failures).
SITE_2 = {
    "platform": "custom",
    "url": "https://careers.example-labs.test/",
    "enabled": True,
    "mode": "llm_browser",
}
HOST, HOST_2 = "jobs.example-robotics.test", "careers.example-labs.test"


def search(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title_include": {
            "terms": ["ai engineer", "forward deployed", "genai", "مهندس"],
            "advanced_regex": False,
        },
        "title_exclude": {"terms": ["account executive", "sales"], "advanced_regex": False},
        "location": {"terms": ["paris", "london", "europe", "remote"], "advanced_regex": False},
        "contract_types": [],
        "days": 30,
        "interactive_sites": [SITE],
        "never_apply": ["No security roles: security, cyber (test rule)"],
    }
    data.update(overrides)
    return data


def set_profile(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    search_section: dict[str, Any] | None,
    skills: dict[str, Any] | None = SKILLS,
) -> None:
    sections: dict[str, Any] = {}
    if search_section is not None:
        sections["search"] = search_section
    if skills is not None:
        sections["skills"] = skills
    with factory() as db, db.begin():
        existing = db.get(Profile, user_id)
        if existing is None:
            db.add(Profile(user_id=user_id, sections=sections, revision=1))
        else:
            existing.sections = sections


def now() -> datetime:
    return datetime.now(UTC)


def new_sweep(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    trigger: SweepTrigger = SweepTrigger.manual,
) -> uuid.UUID:
    """A running sweep of the person's ticked websites, as POST /api/jobs/sweeps starts it."""
    with factory() as db:
        row = sweep.start(db, user_id, trigger, now())
        db.commit()
        return row.id


def finding(
    title: str,
    company: str | None = "Acme Robotics",
    location: str = "Paris, France",
    url: str | None = None,
    text: str | None = None,
    published_on: str | None = None,
) -> finder.VerifiedFinding:
    """A posting a website's agent verified on its detail page."""
    slug = "-".join(title.lower().split())
    return finder.VerifiedFinding(
        title=title,
        location=location,
        url=url or f"https://jobs.example-robotics.test/jobs/{slug}",
        text=text if text is not None else f"{title}. We build agents with Python and LLMs.",
        company=company,
        published_on=published_on,
        evidence={"title": title},
        detail_read_at=now().isoformat(),
    )


def websites(*findings: finder.VerifiedFinding) -> sweep.BrowserHook:
    """A fake of the website Runner: the first ticked website "finds" `findings`, stored through
    the run's commits in batches, as the interactive finder reports them."""
    return each_website(findings)


def each_website(
    *found: Sequence[finder.VerifiedFinding],
    hang_s: float = 0.0,
    hang_at: int = -1,
    errors: dict[int, str] | None = None,
) -> sweep.BrowserHook:
    """A fake of the website Runner over every ticked website in order: website `i` "finds"
    `found[i]` (nothing past the list), stored through the run's commits in batches. `errors`
    ends website `i` with that error code, after what it stored; `hang_s` keeps website `hang_at`
    (the last by default) running that long after its findings are stored (a slow website: the
    time limit, a cancellation, the heartbeat and the progress)."""

    async def hook(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float) -> None:
        del deadline
        sites = [entry for entry in stats.sources if entry.get("host")]
        for index, entry in enumerate(sites):
            entry.update(phase="running", outcome=None, reason=None)
            stats.current_board = entry["source_id"]
            assert plan.commits is not None
            plan.commits.admit(entry["source_id"])
            mine = list(found[index]) if index < len(found) else []
            size = finder.VERIFIED_BATCH_MAX
            for first in range(0, len(mine), size):
                batch = finder.VerifiedBatch(tuple(mine[first : first + size]))
                await plan.commits.save(entry["source_id"], entry["host"], batch)
            await plan.commits.settle()
            await progress()
            if hang_s and index == range(len(sites))[hang_at]:
                await asyncio.sleep(hang_s)
            if errors and index in errors:
                entry["error"] = errors[index]
            sweep.finish_source(entry)
            await progress()

    return hook


def website_runner(tools: Any, leases: Any = None, **options: Any) -> Any:
    """The real website Runner (app/browser/sources) over the browser tests' fakes: `tools` for
    browserd, a recording lease store, a paced clock that never waits."""
    from app.browser.sources import MemoryPacer, Runner
    from tests.browser.fakes import FakeLeases

    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Runner(
        leases or FakeLeases(),
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=random.Random(1),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
        **options,
    )


async def run_async(
    factory: sessionmaker[Session],
    sweep_id: uuid.UUID,
    *findings: finder.VerifiedFinding,
    **options: Any,
) -> None:
    options.setdefault("browser", websites(*findings))
    await sweep.run_sweep(factory, sweep_id, now, **options)


def run(
    factory: sessionmaker[Session],
    sweep_id: uuid.UUID,
    *findings: finder.VerifiedFinding,
    **options: Any,
) -> Sweep:
    asyncio.run(run_async(factory, sweep_id, *findings, **options))
    return read_sweep(factory, sweep_id)


def read_sweep(factory: sessionmaker[Session], sweep_id: uuid.UUID) -> Sweep:
    with factory() as db:
        found = db.get(Sweep, sweep_id)
        assert found is not None
        return found


def jobs_of(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[Job]:
    with factory() as db:
        return list(
            db.execute(
                sa.select(Job)
                .where(Job.user_id == user_id)
                .options(undefer(Job.posting_text))
                .order_by(Job.company, Job.title)
            ).scalars()
        )
