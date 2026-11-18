"""Browser B25: a sweep whose every selected source ended failed or not attempted is Failed."""

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.jobs.models import SweepStatus
from tests.jobs.sweep_helpers import SITE, SITE_2, new_sweep, now, read_sweep, search, set_profile


def sources(*outcomes: str) -> dict:
    return {
        "sources": [
            {"source_id": f"s{i}", "outcome": outcome, "phase": "done"}
            for i, outcome in enumerate(outcomes)
        ]
    }


@pytest.mark.parametrize(
    "outcomes",
    [("failed",), ("not_attempted",), ("failed", "not_attempted", "failed")],
)
def test_no_working_source_is_no_sweep_that_worked(outcomes: tuple[str, ...]) -> None:
    assert not sweep.any_source_worked(sources(*outcomes))


@pytest.mark.parametrize("working", ["partial", "complete", "no_matches"])
def test_one_working_source_is_enough(working: str) -> None:
    assert sweep.any_source_worked(sources("failed", working, "not_attempted"))


def test_a_sweep_without_selected_sources_is_not_judged() -> None:
    assert sweep.any_source_worked({"sources": []})
    assert sweep.any_source_worked({"sources": [{"outcome": "failed"}]})  # no source_id
    assert " - " not in sweep.NO_SOURCE_WORKED


def finished(
    factory: sessionmaker[Session], alice: uuid.UUID, *outcomes: str, selected: Any = (SITE, SITE_2)
) -> Any:
    """A running sweep of the person's websites that ends with `outcomes` (one per website)."""
    set_profile(factory, alice, search(interactive_sites=list(selected)))
    sweep_id = new_sweep(factory, alice)
    stats = sweep.Stats(
        sources=[dict(entry) for entry in read_sweep(factory, sweep_id).stats["sources"]]
    )
    for entry, outcome in zip(stats.sources, outcomes, strict=True):
        entry["outcome"] = outcome
    sweep.finish_done(factory, sweep_id, stats, now())
    return read_sweep(factory, sweep_id)


def test_finish_done_ends_failed_when_no_selected_website_worked(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    row = finished(factory, alice, "failed", "not_attempted")
    assert (row.status, row.error) == (SweepStatus.failed, sweep.NO_SOURCE_WORKED)
    assert row.finished_at is not None


@pytest.mark.parametrize("working", ["complete", "partial", "no_matches"])
def test_finish_done_ends_done_when_one_website_worked(
    factory: sessionmaker[Session], alice: uuid.UUID, working: str
) -> None:
    row = finished(factory, alice, "failed", working)
    assert (row.status, row.error) == (SweepStatus.done, None)


def test_a_sweep_whose_only_working_source_is_the_linkedin_guest_fallback_is_not_failed(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """J20 with B25: LinkedIn's public job search stood in for the website's browser search, which
    failed; the stand-in entry has no place among the selected websites yet it worked."""
    set_profile(factory, alice, search(interactive_sites=[SITE]))
    sweep_id = new_sweep(factory, alice)
    stats = sweep.Stats(
        sources=[dict(entry) for entry in read_sweep(factory, sweep_id).stats["sources"]]
    )
    stats.sources[0]["outcome"] = "failed"
    stats.sources.append(
        {
            "source_id": sweep._source_id("api", SITE["url"]),
            "mode": "api",
            "kind": "linkedin",
            "phase": "done",
            "outcome": "complete",
            "fallback": "guest",
            "fallback_after": "linkedin_signin",
            "error": None,
        }
    )
    sweep.finish_done(factory, sweep_id, stats, now())
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.done, None)
