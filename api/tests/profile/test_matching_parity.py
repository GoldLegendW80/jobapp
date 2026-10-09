"""The shared parity fixture (fixtures/matching_parity.json) holds what the Python matcher and regex
checks decide on a set of strings; web/src/features/profile/matching.test.ts runs the same file
against the TypeScript port of the Skills "Try it" box and the client regex check. This side
proves the fixture still says what the server does, so the browser test cannot drift from it."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.matching import matches
from app.profile.schemas import TermList, check_regex, has_nested_quantifier

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "matching_parity.json").read_text(encoding="utf-8")
)


def _id(case: dict[str, Any]) -> str:
    return repr(case.get("terms", case.get("pattern")))[:60]


@pytest.mark.parametrize("case", FIXTURE["matching"], ids=_id)
def test_matching(case: dict[str, Any]) -> None:
    assert matches(case["terms"], case["advanced_regex"], case["text"]) is case["expected"]


@pytest.mark.parametrize("case", FIXTURE["nested_quantifier"], ids=_id)
def test_nested_quantifier(case: dict[str, Any]) -> None:
    assert has_nested_quantifier(case["pattern"]) is case["expected"]


@pytest.mark.parametrize("case", FIXTURE["check_regex"], ids=_id)
def test_check_regex(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert check_regex(case["pattern"]) == case["pattern"]
    else:
        with pytest.raises(ValueError, match=r"regular expression|nested quantifiers"):
            check_regex(case["pattern"])


@pytest.mark.parametrize("case", FIXTURE["joined"], ids=_id)
def test_joined(case: dict[str, Any]) -> None:
    for term in case["terms"]:
        check_regex(term)  # each term is valid alone
    data = {"terms": case["terms"], "advanced_regex": True}
    if case["valid"]:
        TermList.model_validate(data)
    else:
        with pytest.raises(ValidationError, match=r"unique across the list|do not combine"):
            TermList.model_validate(data)


def test_fixture_covers_both_outcomes() -> None:
    for key, field in (("matching", "expected"), ("nested_quantifier", "expected")):
        assert {case[field] for case in FIXTURE[key]} == {True, False}
    assert {case["valid"] for case in FIXTURE["check_regex"]} == {True, False}
    assert {case["valid"] for case in FIXTURE["joined"]} == {True, False}
    assert any(case["advanced_regex"] for case in FIXTURE["matching"])
    assert any(not case["advanced_regex"] for case in FIXTURE["matching"])
