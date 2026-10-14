"""answers: the optional fields the apply module reads (task A3). A section saved before they
existed stays valid and reads with every one empty, `salary_policy` keeps its type and values, the
fields are validated strictly, and they survive a save, an export and an import."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.schemas import AnswersSection
from tests.identity.api_helpers import Api
from tests.profile.conftest import full
from tests.profile.test_api import read, save_ok
from tests.profile.test_export_import import document, export, import_ok

NEW = [
    "preferred_name",
    "pronouns",
    "years_experience_stated",
    "highest_degree",
    "highest_degree_year",
    "willing_onsite",
    "willing_hybrid",
    "previously_applied_default",
    "background_check_consent",
    "reference_check_consent",
    "eeo_policy",
    "eeo_answers",
    "salary_stance",
    "salary_ranges",
]


def legacy() -> dict[str, Any]:
    stored = full("answers")
    for name in NEW:
        stored.pop(name)
    return stored


def test_a_section_saved_before_the_fields_still_validates_with_them_empty() -> None:
    section = AnswersSection.model_validate(legacy())
    assert section.preferred_name is None
    assert section.eeo_policy == "decline"
    assert section.eeo_answers == []
    assert section.salary_ranges == []
    assert section.salary_policy == "Never invent a number."


def test_the_salary_stance_defaults_from_the_paragraph() -> None:
    filled = AnswersSection.model_validate(legacy())
    assert filled.salary_stance is None
    assert filled.salary_stance_of == "sentence"
    assert AnswersSection().salary_stance_of == "leave_blank"
    assert AnswersSection(salary_policy="  ").salary_stance_of == "leave_blank"


def test_a_stored_stance_wins_over_the_default() -> None:
    section = AnswersSection.model_validate({**legacy(), "salary_stance": "leave_blank"})
    assert section.salary_stance_of == "leave_blank"
    assert section.salary_policy == "Never invent a number."


def test_the_complete_sample_is_stored_as_written() -> None:
    assert AnswersSection.model_validate(full("answers")).model_dump(mode="json") == full("answers")


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"eeo_policy": "guess"}, "decline"),
        ({"salary_stance": "high"}, "leave_blank"),
        ({"willing_onsite": "yes"}, "valid boolean"),
        ({"highest_degree_year": "2022"}, "valid integer"),
        ({"highest_degree_year": 1800}, "greater than or equal"),
        ({"eeo_answers": [{"topic": "religion", "answer": "x"}]}, "gender"),
        (
            {"eeo_answers": [{"topic": "age", "answer": "a"}, {"topic": "age", "answer": "b"}]},
            "duplicate topic",
        ),
        ({"salary_ranges": [{"currency": "eur", "minimum": 1}]}, "pattern"),
        ({"salary_ranges": [{"currency": "EUR", "minimum": -1}]}, "greater than or equal"),
        ({"salary_ranges": [{"currency": "EUR", "minimum": "5"}]}, "valid integer"),
        ({"salary_ranges": [{"currency": "EUR", "minimum": 9, "maximum": 3}]}, "below the minimum"),
        (
            {
                "salary_ranges": [
                    {"currency": "EUR", "minimum": 1},
                    {"currency": "EUR", "minimum": 2},
                ]
            },
            "duplicate currency",
        ),
        ({"salary_ranges": [{"currency": "EUR", "minimum": 1, "period": "week"}]}, "year"),
        ({"unknown_field": 1}, "Extra inputs"),
    ],
)
def test_the_fields_are_validated_strictly(change: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        AnswersSection.model_validate({**full("answers"), **change})
    assert fragment in str(caught.value)


def test_text_fields_are_cleaned_and_an_empty_one_is_null() -> None:
    section = AnswersSection.model_validate(
        {**legacy(), "preferred_name": "  Ana  ", "pronouns": "   ", "highest_degree": ""}
    )
    assert section.preferred_name == "Ana"
    assert section.pronouns is None
    assert section.highest_degree is None


def test_a_range_may_leave_the_maximum_out() -> None:
    section = AnswersSection.model_validate(
        {**legacy(), "salary_ranges": [{"currency": "CHF", "minimum": 90000}]}
    )
    assert section.salary_ranges[0].maximum is None
    assert section.salary_ranges[0].period == "year"


def test_a_save_stores_and_returns_the_fields(api: Api, alice: str) -> None:
    saved = save_ok(api, alice, "answers", full("answers"), 0)
    assert saved["sections"]["answers"] == full("answers")
    again = save_ok(api, alice, "answers", saved["sections"]["answers"], saved["revision"])
    assert again["sections"]["answers"] == full("answers")
    assert read(api, alice)["sections"]["answers"] == full("answers")


def test_a_legacy_section_saves_and_reads_with_the_defaults(api: Api, alice: str) -> None:
    saved = save_ok(api, alice, "answers", legacy(), 0)
    stored = saved["sections"]["answers"]
    assert stored["salary_policy"] == "Never invent a number."
    assert stored["eeo_policy"] == "decline"
    assert stored["salary_stance"] is None
    assert stored["salary_ranges"] == []


def test_export_then_import_round_trips_the_fields(api: Api, alice: str, bob: str) -> None:
    sections = {name: full(name) for name in ("identity", "experience", "facts", "skills")}
    sections["search"] = full("search")
    sections["answers"] = full("answers")
    import_ok(api, alice, document(sections))
    file = export(api, alice)
    assert file["sections"]["answers"] == full("answers")
    imported = import_ok(api, bob, copy.deepcopy(file))
    assert imported["sections"]["answers"] == full("answers")
    assert export(api, bob)["sections"] == file["sections"]
