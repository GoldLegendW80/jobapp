"""Browser B25: a sweep whose every selected source ended failed or not attempted is Failed."""

import pytest

from app.jobs import sweep


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
