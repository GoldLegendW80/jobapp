"""The checks of a document (D2): `checks.run` over invented content and sources."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import checks, rules
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from app.mail.factcheck_rules import Sources
from tests.documents.conftest import base_cv, posting, view_of, written

OFF = DocumentSettings()
DASH_ON = DocumentSettings(no_dash_separators=True)


def _sources(kind: c.Kind = "cv") -> Sources:
    base = base_cv("alex")
    return sources_for(view_of("alex", base), base, None, posting("lumen"), kind=kind)


def _cv(bullet: str) -> dict[str, Any]:
    data = copy.deepcopy(written("cv_short"))
    data["experience"][0]["bullets"] = [bullet]
    return data


def _claims(kind: c.Kind, data: dict[str, Any], settings: DocumentSettings = OFF) -> list[str]:
    result = checks.run(kind, data, _sources(kind), settings)
    assert result.content is not None
    return [checks.excerpt(result.content, issue) for issue in result.claims]


CLAIMS = [
    # (case, bullet, the flagged texts)
    ("3.5 years against a profile that says so", "About 3.5 years of engineering.", []),
    ("4 years against it", "About 4 years of engineering.", ["4 years"]),
    ("4+ years against it", "4+ years of engineering.", ["4+ years"]),
    ("a number copied from the posting", "Ran 300 services.", ["300"]),
    ("a team size copied from the posting", "Led a team of 14 engineers.", ["14"]),
    ("a claim present only in the base CV", "Cut build times by 30% with a shared cache.", []),
    ("a claim only in the facts", "Moved 40 services to Kubernetes.", []),
    ("a skills term after with", "Worked with Kubernetes and Terraform.", []),
    ("an invented employer", "Worked at Globex Industries on billing.", ["Globex Industries"]),
    ("a fixed employer", "Built tools at Sample Labs.", []),
    ("an employer only the base CV states", "Worked at North Shop on weekends.", []),
    ("an invented percentage", "Cut costs by 45% in a quarter.", ["45%"]),
    ("an invented year", "Shipped the platform in 2015.", ["2015"]),
    ("a year of the fixed facts", "At Example Corp since 2023.", []),
]


@pytest.mark.parametrize(
    ("bullet", "flagged"), [case[1:] for case in CLAIMS], ids=[case[0] for case in CLAIMS]
)
def test_claims_against_the_sources(bullet: str, flagged: list[str]) -> None:
    assert _claims("cv", _cv(bullet)) == flagged


def test_the_postings_company_named_in_a_letter_passes() -> None:
    data = copy.deepcopy(written("letter_typical"))
    data["paragraphs"] = ["I would be glad to work for Lumen Example on its platform."]
    assert _claims("letter", data) == []
    data["paragraphs"] = ["I would be glad to work for Initrode Example on its platform."]
    assert _claims("letter", data) == ["Initrode Example"]


def test_a_title_not_in_the_profile_is_flagged_in_french_too() -> None:
    base = base_cv("alex")
    sources = sources_for(view_of("alex", base), base, None, posting("brise"), kind="letter")
    data = {**written("letter_fr"), "headline": "Directeur technique"}
    result = checks.run("letter", data, sources, OFF)
    assert [(i.field, i.code) for i in result.claims] == [("headline", "title_not_in_profile")]


def test_a_dash_separator_is_an_issue_only_with_the_setting_on() -> None:
    data = _cv("Run the platform — from commit to production, full-stack.")
    assert checks.run("cv", data, _sources(), OFF).separators == ()
    found = checks.run("cv", data, _sources(), DASH_ON).separators
    assert [(i.field, i.code) for i in found] == [("experience.0.bullets.0", rules.DASH_SEPARATOR)]
    assert checks.run("cv", _cv("A full-stack engineer."), _sources(), DASH_ON).separators == ()


def test_a_banned_phrase_with_other_case_and_accents_is_a_claim() -> None:
    settings = DocumentSettings(banned_phrases=("passionné",))
    data = _cv("Toujours PASSIONNE par les plateformes.")
    result = checks.run("cv", data, _sources(), settings)
    assert [(i.code, checks.excerpt(result.content, i)) for i in result.claims] == [
        (rules.BANNED_PHRASE, "PASSIONNE")
    ]


def test_invalid_content_is_checked_no_further() -> None:
    data = {**written("cv_short"), "highlights": ["a", "b", "c", "d"]}
    result = checks.run("cv", data, _sources(), OFF)
    assert result.content is None
    assert [(i.field, i.code) for i in result.invalid] == [("highlights", "too_many")]
    assert result.claims == ()


def test_an_unknown_employer_key_is_invalid() -> None:
    data = copy.deepcopy(written("cv_typical"))
    data["experience"][1]["employer_key"] = "made-up-employer"
    keys = view_of("alex", base_cv("alex")).employer_keys
    result = checks.run("cv", data, _sources(), OFF, employer_keys=keys)
    assert [(i.field, i.code) for i in result.invalid] == [
        ("experience.1.employer_key", rules.UNKNOWN_EMPLOYER)
    ]


def test_issues_are_ordered_by_level_then_field_then_position() -> None:
    data = copy.deepcopy(written("cv_typical"))
    data["headline"] = "Platform Engineer — since 2015"
    data["experience"][0]["bullets"][0] = "Led 9 people at Globex Industries — for 4 years."
    result = checks.run("cv", data, _sources(), DASH_ON, soft_limits=rules.SoftLimits(headline=10))
    levels = [i.level for i in result.issues]
    assert levels == sorted(levels, key=rules.LEVELS.index)
    order = [path for path, _ in c.text_fields(result.content)]
    claims = [(order.index(i.field), i.start) for i in result.claims]
    assert claims == sorted(claims)
    assert {i.field for i in result.claims} >= {"headline", "experience.0.bullets.0"}
    assert result.notes[0].code == rules.OVER_SOFT_LENGTH


def test_run_is_deterministic_and_takes_a_model_or_data() -> None:
    data = written("cv_long")
    first = checks.run("cv", data, _sources(), DASH_ON)
    for _ in range(3):
        assert checks.run("cv", data, _sources(), DASH_ON) == first
    model = c.parse("cv", data)
    assert checks.run("cv", model, _sources(), DASH_ON).issues == first.issues
    shuffled = Sources(**{k: tuple(reversed(v)) for k, v in vars_of(_sources()).items()})
    assert checks.run("cv", data, shuffled, DASH_ON).issues == first.issues


def vars_of(sources: Sources) -> dict[str, tuple[str, ...]]:
    return {name: getattr(sources, name) for name in Sources.__dataclass_fields__}


@pytest.mark.parametrize(
    "name", ["cv_short", "cv_typical", "cv_long", "letter_typical", "letter_fr"]
)
def test_one_joined_check_gives_the_issues_of_one_check_per_field(name: str) -> None:
    kind: c.Kind = "letter" if name.startswith("letter") else "cv"
    model = c.parse(kind, written(name))
    sources = _sources()
    fields = [(path, text) for path, text in c.text_fields(model) if text]
    per_field = [
        (found.field, found.start, found.end, found.code)
        for field in fields
        for found in checks.fact_issues([field], sources)
    ]
    joined = [(i.field, i.start, i.end, i.code) for i in checks.fact_issues(fields, sources)]
    assert sorted(joined) == sorted(per_field)


def test_a_claim_at_the_edge_of_a_field_stays_in_its_field() -> None:
    fields = [("highlights.0", "Worked at"), ("highlights.1", "Globex Industries 300")]
    found = checks.fact_issues(fields, _sources())
    assert {i.field for i in found} == {"highlights.1"}
    assert all(0 <= i.start < i.end <= len(fields[1][1]) for i in found)


def test_20_kb_of_content_is_checked() -> None:
    # The plan's 200 ms is measured, not asserted (no clock bounds in tests): see the PR.
    sentence = "Moved 40 services to Kubernetes at Example Corp with Python. "
    roles = [{"employer_key": f"e{i}", "bullets": [(sentence * 4)[:185]] * 8} for i in range(12)]
    data = {"summary": (sentence * 10)[:600], "experience": roles}
    model = c.parse("cv", data)
    assert len(c.canonical_json(model)) > 19_000
    result = checks.run("cv", model, _sources(), DASH_ON)
    assert result.content is not None
    assert result.invalid == ()
