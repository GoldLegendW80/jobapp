"""The sweep keeps exactly the postings the engine kept.

fixtures/ats/filter_cases.json holds every posting of six real boards (2,996 of them) with the
engine's decisions, made by build_ats_fixtures.py with the regexes of engine/boards.json: by the
title, exclude, per-board exclude and location regexes, and with the 30 day window on a fixed
day. Here the same postings go through sweep.SearchFilter built from the search section of
tests/profile/fixtures/owner_export.json, the importer output of a copy of that boards.json
(tests/profile/fixtures/owner_sources/engine/boards.json): every decision must be the same.

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
    (HERE.parent / "profile" / "fixtures" / "owner_export.json").read_text(encoding="utf-8")
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
# No fallback (issue #11): the profile refuses a list that does not compile, so the matcher never
# drops broken terms. A list that still fails reaches the claim, which fails the sweep
# (tests/jobs/test_sweep.py, test_a_list_that_stops_compiling_fails_the_sweep_closed).


def test_a_list_that_does_not_compile_raises_instead_of_dropping_terms() -> None:
    import re

    from app.jobs.sweep import term_matcher

    with pytest.raises(re.error):
        term_matcher(["(?P<x>agent)", "(?P<x>llm)"], True)
    with pytest.raises(re.error):
        term_matcher(["security", "("], True)  # an exclude list is never weakened


def test_an_empty_list_is_no_restriction_and_a_valid_one_compiles() -> None:
    from app.jobs.sweep import term_matcher

    assert term_matcher([], True) is None
    pattern = term_matcher(["agent", "llm"], False)
    assert pattern is not None
    assert pattern.search("LLM engineer") is not None
    assert pattern.search("Accountant") is None


def test_the_profile_refuses_the_lists_the_matcher_would_not_compile() -> None:
    """What makes the fallback dead: the same lists never validate."""
    for terms in (["(?P<x>agent)", "(?P<x>llm)"], ["("], ["[a-"]):
        broken = {"terms": terms, "advanced_regex": True}
        data = {**EXPORT["sections"]["search"], "title_exclude": broken}
        with pytest.raises(ValueError, match="title_exclude"):
            SearchSection.model_validate(data)
