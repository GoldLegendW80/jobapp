"""Summary text compares typed yes/no answers in the vocabulary discovery supports."""

import pytest

from app.apply.ats.base import parse_snapshot, summary_answer, summary_matches, summary_text


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


@pytest.mark.parametrize("inline", [True, False])
def test_named_summary_row_keeps_inline_and_nested_value_but_excludes_label(inline):
    row = (
        '- group "First name": Example Cedar'
        if inline
        else '- group "First name":\n  - text: Example Cedar'
    )
    group = next(node for node in parse_snapshot(row).walk() if node.role == "group")
    assert summary_text(group) == "Example Cedar"
