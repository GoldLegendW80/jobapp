"""Smoke test of the website sweep helper."""

import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.jobs.models import SweepStatus
from tests.jobs.sweep_helpers import finding, jobs_of, new_sweep, run, search, set_profile


def test_a_website_sweep_stores_its_findings(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    row = run(
        factory,
        new_sweep(factory, alice),
        finding("AI Engineer"),
        finding("Security Engineer"),
        *[finding(f"GenAI Engineer {n}") for n in range(6)],
    )
    assert row.status is SweepStatus.done, row.error
    assert row.stats["new"] == 8
    assert row.stats["boards_total"] == 0
    assert row.stats["skipped_by_rule"] == 1
    assert len(jobs_of(factory, alice)) == 8
