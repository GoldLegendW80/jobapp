"""Scoring with a stored term list that no longer validates and does not compile (saved before
the profile refused such lists): that list is skipped with one warning, the rest still scores."""

import logging
import re

import pytest

from app.jobs import scoring
from app.jobs.scoring import ScoreResult, score
from app.profile.schemas import Category, Gate, SkillsSection

BROKEN = ["(?P<n>agent)", "(?P<n>python)"]  # each valid alone, the joined list is not


def legacy(**lists: list[object]) -> SkillsSection:
    """A section as read from the database without validation (model_construct)."""
    base = {"years_experience": None, "have": [], "adjacent": [], "none": [], "gates": []}
    base.update(lists)
    return SkillsSection.model_construct(**base)  # type: ignore[arg-type]


def category(name: str, terms: list[str], advanced: bool = True) -> Category:
    return Category.model_construct(name=name, terms=terms, advanced_regex=advanced)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    scoring._compiled.cache_clear()


def test_the_broken_list_really_does_not_compile() -> None:
    with pytest.raises(re.error):
        re.compile("|".join(f"(?:{t})" for t in BROKEN))


def test_a_broken_category_is_skipped_the_others_score(caplog: pytest.LogCaptureFixture) -> None:
    section = legacy(
        have=[category("broken", BROKEN), category("python", ["python"], advanced=False)],
        none=[category("rust", ["rust"], advanced=False)],
    )
    with caplog.at_level(logging.WARNING, logger="app.jobs.scoring"):
        assert score("Agents in Python.", section) == ScoreResult(100, ["python"], [])
        assert score("Python and Rust.", section) == ScoreResult(
            round(100 * (1 - 0.5) / 2), ["python", "rust"], ["rust"]
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # Once per list and process, not once per posting.
    assert len(warnings) == 1
    assert warnings[0].getMessage() == "skills term list does not compile, skipped in scoring"
    assert warnings[0].list == "have 1"  # type: ignore[attr-defined]
    # Never the terms themselves (profile contents stay out of the logs).
    extra = {
        k: v for k, v in warnings[0].__dict__.items() if k in ("msg", "args", "list", "position")
    }
    assert "python" not in str(extra)
    assert "'n'" not in str(extra)
    assert warnings[0].position == 24  # type: ignore[attr-defined]


def test_a_broken_gate_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    gate = Gate.model_construct(name="g", terms=BROKEN, advanced_regex=True, penalty=25)
    section = legacy(have=[category("python", ["python"], advanced=False)], gates=[gate])
    with caplog.at_level(logging.WARNING, logger="app.jobs.scoring"):
        assert score("python agent", section) == ScoreResult(100, ["python"], [])
    assert [r.list for r in caplog.records] == ["gate 1"]  # type: ignore[attr-defined]


def test_valid_lists_are_compiled_once() -> None:
    section = legacy(have=[category("python", ["python"], advanced=False)])
    for _ in range(5):
        score("python", section)
    info = scoring._compiled.cache_info()
    assert info.misses == 1
    assert info.hits == 4
