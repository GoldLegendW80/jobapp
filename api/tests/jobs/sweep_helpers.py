"""Helpers of the sweep tests: a search section over fake boards, profiles, running a sweep."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.jobs import ats, sweep
from app.jobs.models import Job, Sweep, SweepTrigger
from app.profile.models import Profile
from tests.jobs.fake_ats import FakeAts
from tests.jobs.test_scoring import skills as make_skills

SKILLS = make_skills().model_dump(mode="json")


# A job website of the person's own, ticked: every search runs the websites alone (2026-10-28).
SITE = {
    "platform": "custom",
    "url": "https://jobs.example-robotics.test/",
    "enabled": True,
    "mode": "llm_browser",
}


def board(org: str, ats_name: str = "ashby", company: str | None = None, **extra: Any) -> dict:
    return {
        "company": company or org.removeprefix("fake-").title(),
        "ats": ats_name,
        "org": org,
        "enabled": True,
        "extra_exclude": {"terms": [], "advanced_regex": False},
        **extra,
    }


def search(*boards: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title_include": {
            "terms": ["ai engineer", "forward deployed", "genai", "مهندس"],
            "advanced_regex": False,
        },
        "title_exclude": {"terms": ["account executive", "sales"], "advanced_regex": False},
        "location": {"terms": ["paris", "london", "europe", "remote"], "advanced_regex": False},
        "contract_types": [],
        "days": 30,
        "boards": list(boards),
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
    """A running sweep of every enabled source, boards included: the board engine's internal
    start, which no search uses since the owner's decision of 2026-10-28 (websites only)."""
    with factory() as db:
        row = sweep.start(db, user_id, trigger, now(), websites_only=False)
        db.commit()
        return row.id


def client(fake: FakeAts, **options: Any) -> ats.AtsClient:
    async def no_sleep(_: float) -> None:
        return None

    settings: dict[str, Any] = {"timeout_s": 1.0, "sleep": no_sleep}
    settings.update(options)
    return ats.AtsClient(
        tz=sweep.SWEEP_TZ, stub_base_url=fake.url, stub_org_prefix="fake-", **settings
    )


async def run_async(
    factory: sessionmaker[Session], sweep_id: uuid.UUID, fake: FakeAts, **options: Any
) -> None:
    client_options = options.pop("client_options", {})
    async with client(fake, **client_options) as c:
        await sweep.run_sweep(factory, sweep_id, c, now, **options)


def run(
    factory: sessionmaker[Session], sweep_id: uuid.UUID, fake: FakeAts, **options: Any
) -> Sweep:
    asyncio.run(run_async(factory, sweep_id, fake, **options))
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


@pytest.fixture
def fake_ats() -> Iterator[FakeAts]:
    with FakeAts(slow_s=2.5) as server:
        yield server
