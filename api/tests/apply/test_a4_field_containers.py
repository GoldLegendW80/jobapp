"""Only field containers lend their labels to controls; page actions do not."""

from pathlib import Path

import pytest

from app.apply.ats import base

ROOT = Path(__file__).resolve().parents[3]


def discover(snapshot: str) -> base.Discovery:
    return base.discover_fields(base.parse_snapshot(snapshot), base.SUBMIT_NAMES)


def indent(snapshot: str) -> str:
    return "\n".join("  " + line for line in snapshot.splitlines())


def test_native_workable_footer_does_not_fabricate_a_whole_form_question() -> None:
    snapshot = (ROOT / "tests/ats_forms/a8/snapshots/workable-initial.txt").read_text()
    found = discover(snapshot)
    assert [(f.label, f.kind) for f in found.fields] == [
        ("First name", "text"),
        ("Last name", "text"),
        ("Email", "text"),
        ("Phone", "text"),
        ("Resume", "file"),
        ("How did you hear about us?", "select"),
        ("Summary", "text"),
    ]
    assert found.skipped == []
    assert found.submit_refs == ["e26"]


@pytest.mark.parametrize("container", ["generic", "group", 'group "Team preference *"'])
@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("peers", [False, True])
def test_genuine_unknown_button_fields_keep_their_nearest_label(
    container: str, depth: int, peers: bool
) -> None:
    buttons = '- button "Platform" [ref=e9]'
    if peers:
        buttons += '\n- generic:\n  - button "Research" [ref=e10]'
    for _ in range(depth):
        buttons = "- generic:\n" + indent(buttons)
    snapshot = '- main:\n  - heading "Application"\n' + indent(
        f"- {container}:\n  - generic: Team preference *\n" + indent(buttons)
    )
    found = discover(snapshot)
    assert found.fields == []
    assert [(s.label, s.role, s.reason) for s in found.skipped] == [
        ("Team preference", "button", "unsupported_widget")
    ]


@pytest.mark.parametrize(
    "wrapper", ["main", "form", "banner", "contentinfo", "navigation", "complementary", "region"]
)
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_page_and_form_action_wrappers_do_not_supply_field_labels(wrapper: str, depth: int) -> None:
    action = '- button "Details" [ref=e9]'
    for _ in range(depth):
        action = "- generic:\n" + indent(action)
    snapshot = f"- {wrapper}:\n  - generic: Application\n" + indent(action)
    found = discover(snapshot)
    assert found.fields == []
    assert found.skipped == []


@pytest.mark.parametrize("container", ["generic", 'group "Application"'])
@pytest.mark.parametrize(
    "other",
    [
        '- textbox "Email" [ref=e3]',
        '- combobox "Location" [ref=e3]',
        '- checkbox "Consent" [ref=e3]',
        '- generic:\n  - generic: Resume\n  - button "Upload File" [ref=e3]',
        '- group "Office":\n  - button "Paris" [ref=e3]',
        '- generic:\n  - generic: Office\n  - generic:\n    - button "Paris" [ref=e3]',
    ],
)
def test_generic_form_wrappers_cannot_lend_another_fields_label_to_an_action(
    container: str, other: str
) -> None:
    snapshot = (
        f'- {container}:\n  - generic: Application\n{indent(other)}\n  - button "Details" [ref=e9]'
    )
    found = discover(snapshot)
    assert all(
        s.label not in ("Application", "Application Resume", "Application Office")
        for s in found.skipped
    )
    assert all(f.ref != "e9" for f in found.fields)
    assert not any(s.label.startswith("Application") for s in found.skipped)


@pytest.mark.parametrize("wrapper", ["generic", 'group "Application"'])
def test_page_heading_is_not_a_loose_action_label(wrapper: str) -> None:
    snapshot = f'- {wrapper}:\n  - heading: Careers at Example\n  - button "Details" [ref=e9]'
    found = discover(snapshot)
    assert found.fields == []
    assert found.skipped == []


@pytest.mark.parametrize("wrapper", ["main", "form", "contentinfo", "banner"])
def test_real_fields_inside_landmarks_are_still_discovered(wrapper: str) -> None:
    snapshot = f"""- {wrapper}:
  - generic:
    - generic: Resume *
    - button "Upload File" [ref=e1]
    - button "Dropbox" [ref=e2]
    - generic: cv.pdf
  - group "May we contact you? *":
    - generic: May we contact you?
    - generic:
      - button "Yes" [ref=e3]
      - button "No" [ref=e4]
  - group "Unknown question":
    - button "Choice" [ref=e5]
  - slider "Desired amount" [ref=e6]
  - button "Submit application" [ref=e7]
"""
    found = discover(snapshot)
    assert [(f.label, f.kind, f.required) for f in found.fields] == [
        ("Resume", "file", True),
        ("May we contact you?", "yes_no", True),
    ]
    assert found.fields[0].value == "cv.pdf"
    assert {(s.label, s.reason) for s in found.skipped} == {
        ("Unknown question", "unsupported_widget"),
        ("Desired amount", "unsupported_widget"),
    }
    assert found.submit_refs == ["e7"]


@pytest.mark.parametrize("cut", ["", "\n[snapshot cut]"])
def test_ambiguous_and_unlabelled_controls_stay_unresolved(cut: str) -> None:
    found = discover(
        "- main:\n  - generic: Application\n  - textbox [ref=e1]\n  - listbox [ref=e2]" + cut
    )
    assert found.fields == []
    assert {(s.label, s.role, s.reason) for s in found.skipped} == {
        ("", "textbox", "no_label"),
        ("", "listbox", "unsupported_widget"),
    }


@pytest.mark.parametrize(
    "label", ["Subscribe", "Details", "Application", "Équipe souhaitée", "希望するチーム"]
)
@pytest.mark.parametrize("role", ["generic", "paragraph"])
def test_local_unknown_labels_are_not_filtered_by_their_words(label: str, role: str) -> None:
    found = discover(f'- generic:\n  - {role}: {label}\n  - button "Choice" [ref=e1]')
    assert [(s.label, s.reason) for s in found.skipped] == [(label, "unsupported_widget")]


@pytest.mark.parametrize("label", ["Team preference", "Équipe souhaitée", "Subscribe", "Details"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("depth", [0, 1])
@pytest.mark.parametrize("repeated_label", [False, True])
@pytest.mark.parametrize(
    "extra",
    [
        '- link "About choices" [ref=e3]',
        '- heading "Choose a team"',
        '- textbox "Other details" [ref=e3]',
    ],
)
def test_named_local_choices_remain_unresolved_beside_help_and_other_fields(
    label: str, required: bool, depth: int, repeated_label: bool, extra: str
) -> None:
    name = label + (" *" if required else "")
    buttons = '- button "Platform" [ref=e1]\n- button "Research" [ref=e2]'
    if depth:
        buttons = "- generic:\n" + indent(buttons)
    visible_label = f"- generic: {name}\n" if repeated_label else ""
    found = discover(
        f'- main:\n  - group "{name}":\n' + indent(indent(f"{visible_label}{extra}\n{buttons}"))
    )
    assert [(s.label, s.role, s.reason) for s in found.skipped] == [
        (label, "button", "unsupported_widget")
    ]
    assert [f.label for f in found.fields] == (["Other details"] if "textbox" in extra else [])


@pytest.mark.parametrize("label", ["Team preference", "Subscribe", "Details", "希望するチーム"])
@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("extra", ['- link "About choices" [ref=e3]', '- heading "Choose a team"'])
def test_a_single_local_choice_with_help_stays_unresolved(
    label: str, depth: int, extra: str
) -> None:
    button = '- button "Choose an option" [ref=e1]'
    for _ in range(depth):
        button = "- generic:\n" + indent(button)
    found = discover(
        f'- group "{label} *":\n  - generic: {label} *\n{indent(extra)}\n{indent(button)}'
    )
    assert [(s.label, s.reason) for s in found.skipped] == [(label, "unsupported_widget")]


@pytest.mark.parametrize(
    "extra",
    [
        '- link "About choices" [ref=e3]',
        '- heading "Choose a team"',
        '- textbox "Other details" [ref=e3]',
    ],
)
@pytest.mark.parametrize("kind", ["file", "yes_no"])
def test_named_file_and_yes_no_groups_keep_their_local_label(extra: str, kind: str) -> None:
    label = "Resume" if kind == "file" else "May we contact you?"
    buttons = (
        '- button "Upload File" [ref=e1]\n- button "Dropbox" [ref=e2]'
        if kind == "file"
        else '- button "Yes" [ref=e1]\n- button "No" [ref=e2]'
    )
    found = discover(
        f'- group "{label} *":\n  - generic: {label} *\n{indent(extra)}\n'
        f"  - generic:\n{indent(indent(buttons))}"
    )
    assert [(f.label, f.kind, f.required) for f in found.fields if f.ref != "e3"] == [
        (label, kind, True)
    ]
    assert found.skipped == []
