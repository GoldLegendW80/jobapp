"""Summary text compares typed yes/no answers in the vocabulary discovery supports."""

import pytest

from app.apply.ats.base import summary_answer, summary_matches


@pytest.mark.parametrize("value", ["No", False])
def test_normal_and_person_no_match_french_summary_and_refuse_a_late_yes(value):
    approved = summary_answer(value, "yes_no")
    assert summary_matches(approved, "Non")
    assert not summary_matches(approved, "Oui")
    assert not summary_matches(approved, "False")


@pytest.mark.parametrize("kind", ["text", "select", "textarea"])
def test_textual_no_is_not_translated_as_a_boolean(kind):
    approved = summary_answer("No", kind)
    assert summary_matches(approved, "No")
    assert not summary_matches(approved, "Non")
