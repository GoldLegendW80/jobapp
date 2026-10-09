"""The sweep keeps exactly the postings the engine kept.

fixtures/ats/filter_cases.json holds every posting of six real boards (2,996 of them) with the
engine's decisions, made by build_ats_fixtures.py with the regexes of engine/boards.json: by the
title, exclude, per-board exclude and location regexes, and with the 30 day window on a fixed
day. Here the same postings go through sweep.SearchFilter built from Harald's search section as
the profile importer converted it from that boards.json (tests/profile/fixtures/
harald_export.json): every decision must be the same.

When the engine's files are on this machine, the fixture itself is checked against the live
boards.json (the conversion is redone and must give the same search terms).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.jobs import ats
from app.jobs.sweep import SearchFilter
from app.profile.schemas import SearchSection

HERE = Path(__file__).parent
CASES = json.loads((HERE / "fixtures" / "ats" / "filter_cases.json").read_text(encoding="utf-8"))
EXPORT = json.loads(
    (HERE.parent / "profile" / "fixtures" / "harald_export.json").read_text(encoding="utf-8")
)
SEARCH = SearchSection.model_validate(EXPORT["sections"]["search"])
BOARDS = {board.org: board for board in SEARCH.boards}


def decisions() -> list[tuple[list[object], bool, bool]]:
    filt = SearchFilter.of(SEARCH)
    since = filt.since(date.fromisoformat(CASES["today"]))
    assert filt.days == CASES["days"] == 30
    out = []
    for case in CASES["cases"]:
        org, title, location, published, _, _ = case
        exclude = SearchFilter.board_exclude(BOARDS[org])
        terms = filt.terms_reason(title, location, "9999-12-31", exclude, since) is None
        window = filt.terms_reason(title, location, published, exclude, since) is None
        out.append((case, terms, window))
    return out


def test_every_engine_decision_is_reproduced() -> None:
    wrong = [
        (case[0], case[1], case[2])
        for case, terms, window in decisions()
        if (terms, window) != (case[4], case[5])
    ]
    assert wrong == []


def test_the_fixture_exercises_both_outcomes() -> None:
    cases = CASES["cases"]
    assert len(cases) > 2_000
    assert {c[0] for c in cases} == {
        "mistral.ai",
        "openai",
        "anthropic",
        "databricks",
        "palantir",
        "sonarsource",
    }
    assert sum(c[4] for c in cases) >= 20  # kept by the terms
    assert sum(c[5] for c in cases) >= 10  # and within 30 days
    assert sum(c[4] and not c[5] for c in cases) >= 10  # the window decides those


def test_contract_type_step_after_the_engine_filter() -> None:
    """The one step the engine had no equivalent of: contract types (empty list: any)."""
    filt = SearchFilter.of(SEARCH)
    since = filt.since(date(2026, 10, 9))
    posting = ats.Posting(
        title="AI Engineer (Alternance)",
        location="Paris",
        url="https://jobs.example.org/1",
        published="2026-10-08",
        text="",
        remote=False,
    )
    assert SEARCH.contract_types == ["CDI", "CDD", "VIE", "Freelance"]
    assert (
        filt.terms_reason(posting.title, posting.location, posting.published, None, since) is None
    )
    assert filt.reason(posting, None, since) == "contract"
    anything = SearchFilter.of(SEARCH.model_copy(update={"contract_types": []}))
    assert anything.reason(posting, None, since) is None


ROOT_CANDIDATES = [p for p in HERE.resolve().parents if (p / "engine" / "boards.json").is_file()]


@pytest.mark.skipif(not ROOT_CANDIDATES, reason="engine/boards.json is not on this machine")
def test_converted_terms_match_the_live_boards_json() -> None:
    from app.profile.importer import _search

    live = _search(json.loads((ROOT_CANDIDATES[0] / "engine" / "boards.json").read_text()))
    for name in ("title_include", "title_exclude", "location"):
        assert live[name] == EXPORT["sections"]["search"][name], name
    assert {b["org"]: b["extra_exclude"] for b in live["boards"]} == {
        b["org"]: b["extra_exclude"] for b in EXPORT["sections"]["search"]["boards"]
    }


# --- stored lists that do not compile -----------------------------------------------------------


def test_a_list_that_does_not_compile_keeps_its_good_terms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.jobs.sweep import AnyOf, term_matcher

    terms = ["(?P<x>agent)", "(?P<x>llm)", "("]
    matcher = term_matcher(terms, True, "title_include")
    assert isinstance(matcher, AnyOf)
    assert matcher.search("LLM engineer") is not None
    assert matcher.search("Agent builder") is not None
    assert matcher.search("Accountant") is None
    [record] = [r for r in caplog.records if "does not compile" in r.getMessage()]
    assert (record.list, record.terms, record.kept) == ("title_include", 3, 2)
    assert "agent" not in record.getMessage()


def test_a_list_with_no_good_term_matches_nothing() -> None:
    from app.jobs.sweep import term_matcher

    broken = term_matcher(["(", "[a-"], True, "location")
    assert broken is not None
    assert broken.search("Paris") is None
    assert term_matcher([], True, "location") is None  # empty: no restriction


def test_a_broken_include_list_keeps_no_posting() -> None:
    from app.jobs.sweep import term_matcher

    filt = SearchFilter(
        include=term_matcher(["("], True, "title_include"),
        exclude=None,
        location=None,
        contract_types=frozenset(),
        days=30,
    )
    assert filt.terms_reason("AI Engineer", "Paris", "2026-10-08", None, "2026-09-01") == "title"
