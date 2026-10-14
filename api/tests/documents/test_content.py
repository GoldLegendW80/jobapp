"""The content models (D2): the hard limits refused, the canonical form and its hash."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import content as c
from tests.documents.conftest import load, written


def _cv(**fields: Any) -> dict[str, Any]:
    data = copy.deepcopy(written("cv_typical"))
    data.update(fields)
    return data


def _letter(**fields: Any) -> dict[str, Any]:
    data = copy.deepcopy(written("letter_typical"))
    data.update(fields)
    return data


def _errors(kind: c.Kind, data: object) -> list[tuple[str, str]]:
    with pytest.raises(c.ContentInvalidError) as caught:
        c.parse(kind, data)
    return [(error.path, error.code) for error in caught.value.errors]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("written/cv_short", "cv"),
        ("written/cv_typical", "cv"),
        ("written/cv_long", "cv"),
        ("written/letter_typical", "letter"),
        ("written/letter_fr", "letter"),
        ("base_cv_alex", "base"),
        ("base_cv_blake", "base"),
    ],
)
def test_every_fixture_is_valid(name: str, kind: c.Kind) -> None:
    model = c.parse(kind, load(f"{name}.json"))
    assert c.kind_of(model) == kind
    assert c.dump(model)["schema"] == c.SCHEMA


REFUSED = [
    # (case, kind, data, expected (path, code))
    ("a fourth highlight", "cv", _cv(highlights=["a", "b", "c", "d"]), ("highlights", "too_many")),
    (
        "a bullet of 301 characters",
        "cv",
        _cv(experience=[{"employer_key": "example-corp", "bullets": ["x" * 301]}]),
        ("experience.0.bullets.0", "too_long"),
    ),
    (
        "the same employer key twice",
        "cv",
        _cv(
            experience=[
                {"employer_key": "example-corp", "bullets": ["One."]},
                {"employer_key": "example-corp", "bullets": ["Two."]},
            ]
        ),
        ("experience", "duplicate_employer"),
    ),
    (
        "nine bullets for one employer",
        "cv",
        _cv(experience=[{"employer_key": "example-corp", "bullets": ["b"] * 9}]),
        ("experience.0.bullets", "too_many"),
    ),
    (
        "thirteen employers",
        "cv",
        _cv(experience=[{"employer_key": f"e{i}", "bullets": []} for i in range(13)]),
        ("experience", "too_many"),
    ),
    ("a ninth competency", "cv", _cv(competencies=["k"] * 9), ("competencies", "too_many")),
    (
        "a fifth skills group",
        "cv",
        _cv(skills=[{"label": "l", "items": []}] * 5),
        ("skills", "too_many"),
    ),
    ("a headline of 201 characters", "cv", _cv(headline="h" * 201), ("headline", "too_long")),
    ("a summary of 601 characters", "cv", _cv(summary="s" * 601), ("summary", "too_long")),
    ("an empty bullet", "cv", _cv(highlights=["  "]), ("highlights.0", "empty")),
    ("an unknown field", "cv", _cv(salary="90k"), ("salary", "unknown_field")),
    ("a schema from the future", "cv", _cv(schema=2), ("schema", "unknown_value")),
    ("a number for a text", "cv", _cv(headline=7), ("headline", "wrong_type")),
    ("a control character", "cv", _cv(headline="Hello\x07"), ("headline", "control_character")),
    ("a lone surrogate", "cv", _cv(headline="Hi \ud800"), ("headline", "lone_surrogate")),
    (
        "an employer key that is no slug",
        "cv",
        _cv(experience=[{"employer_key": "Example Corp", "bullets": []}]),
        ("experience.0.employer_key", "bad_format"),
    ),
    ("a sixth paragraph", "letter", _letter(paragraphs=["p"] * 6), ("paragraphs", "too_many")),
    (
        "a paragraph of 2001 characters",
        "letter",
        _letter(paragraphs=["p" * 2001]),
        ("paragraphs.0", "too_long"),
    ),
    ("a CV field in a letter", "letter", _letter(highlights=[]), ("highlights", "unknown_field")),
    ("a fixed block on a posting CV", "cv", _cv(fixed={}), ("fixed", "unknown_field")),
]


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [case[1:] for case in REFUSED],
    ids=[case[0] for case in REFUSED],
)
def test_hard_limits_are_refused_with_the_field_path(
    kind: c.Kind, data: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert expected in _errors(kind, data)


def test_a_bullet_of_300_characters_is_accepted() -> None:
    data = _cv(experience=[{"employer_key": "example-corp", "bullets": ["x" * 300]}])
    assert c.parse("cv", data).experience[0].bullets[0] == "x" * 300


def test_the_whole_content_is_capped_at_20_kb() -> None:
    # Every field within its own limit, the whole beyond 20 KB.
    roles = [{"employer_key": f"e{i}", "bullets": ["b" * 290] * 8} for i in range(12)]
    assert _errors("cv", _cv(experience=roles)) == [("", "content_too_large")]


def test_a_huge_submit_is_refused_before_validation() -> None:
    assert _errors("cv", _cv(headline="h" * 1_000_000)) == [("", "content_too_large")]


def test_the_error_message_never_quotes_the_value() -> None:
    secret = "zq-planted-value-that-must-not-leak"
    with pytest.raises(c.ContentInvalidError) as caught:
        c.parse("cv", _cv(headline=secret * 20, wrong=secret))
    assert all(secret not in error.message for error in caught.value.errors)
    assert secret not in str(caught.value)


def test_empty_sections_are_valid_and_default() -> None:
    model = c.parse("cv", {})
    assert (model.headline, model.highlights, model.experience, model.skills) == ("", [], [], [])
    letter = c.parse("letter", {"schema": 1})
    assert letter.paragraphs == []


def test_strings_are_normalised_not_corrected() -> None:
    model = c.parse(
        "cv",
        {
            "headline": "  Platform\t\tEngineer   for  Example Corp  ",
            "summary": "Line one  \r\n  line two",
            "highlights": ["Café owner"],  # e and a combining acute: NFC
        },
    )
    assert model.headline == "Platform Engineer for Example Corp"
    assert model.summary == "Line one\nline two"
    assert model.highlights == ["Café owner"]


def test_french_and_english_text_is_kept_as_written() -> None:
    model = c.parse("letter", written("letter_fr"))
    assert model.recipient == "Équipe de recrutement, Brise Exemple"
    assert "l'équipe paiements" in model.paragraphs[0]


def test_the_hash_is_canonical_and_depends_on_the_kind() -> None:
    data = written("cv_typical")
    reordered = dict(reversed(list(data.items())))
    a, b = c.parse("cv", data), c.parse("cv", reordered)
    assert c.canonical_json(a) == c.canonical_json(b)
    assert c.content_hash("cv", a) == c.content_hash("cv", b)
    assert len(c.content_hash("cv", a)) == 32
    assert c.content_hash("cv", a) != c.content_hash("base", a)
    changed = c.parse("cv", {**data, "headline": data["headline"] + "!"})
    assert c.content_hash("cv", changed) != c.content_hash("cv", a)


def test_text_fields_list_the_printed_fields_in_reading_order() -> None:
    model = c.parse("cv", written("cv_short"))
    assert [path for path, _ in c.text_fields(model)] == [
        "headline",
        "summary",
        "highlights.0",
        "competencies.0",
        "competencies.1",
        "experience.0.bullets.0",
        "skills.0.label",
        "skills.0.items.0",
        "skills.0.items.1",
    ]
    letter = c.parse("letter", written("letter_typical"))
    paths = [path for path, _ in c.text_fields(letter)]
    assert paths[:4] == ["headline", "recipient", "subject", "salutation"]
    assert paths[-1] == "closing"
    assert "target" not in paths


def test_a_base_cv_keeps_its_fixed_block() -> None:
    model = c.parse("base", load("base_cv_alex.json"))
    assert isinstance(model, c.BaseCvContent)
    assert [e.key for e in model.fixed.employers] == ["example-corp", "sample-labs", "north-shop"]
    assert model.fixed.employers[0].end is None
    bad = load("base_cv_alex.json")
    bad["fixed"]["employers"][0]["start"] = "2023-13"
    assert ("fixed.employers.0.start", "bad_format") in _errors("base", bad)
