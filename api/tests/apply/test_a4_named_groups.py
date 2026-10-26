"""Named accessible groups repeat their visible labels in Chromium's AI snapshot."""

from pathlib import Path

import pytest

from app.apply.ats import base
from app.apply.ats.ashby import Ashby

SNAPSHOTS = Path(__file__).resolve().parents[3] / "tests/ats_forms/a4/snapshots"


def test_pinned_chromium_named_ashby_groups_keep_their_field_labels() -> None:
    root = base.parse_snapshot((SNAPSHOTS / "ashby-named-groups.txt").read_text())
    found = base.discover_fields(root, Ashby.submit_names)
    assert Ashby().anchor_problem(found) == ""
    assert [(f.label, f.kind, f.required) for f in found.fields] == [
        ("Full name", "text", True),
        ("Email", "text", True),
        ("Phone", "text", False),
        ("Current location", "combobox", True),
        ("Resume", "file", True),
        ("Cover letter", "file", False),
        ("Are you legally authorised to work in the European Union?", "yes_no", True),
        ("Will you now or in the future require visa sponsorship?", "yes_no", True),
        ("Why Example Robotics?", "text", True),
        ("LinkedIn profile", "text", False),
        ("I agree to the processing of my data for this application.", "checkbox", True),
    ]
    assert [(s.label, s.reason) for s in found.skipped] == [("Autofill from resume", "autofill")]
    assert found.submit_refs == ["e53"]


@pytest.mark.parametrize(
    "label",
    [
        "Resume",
        "Résumé/CV",
        "Cover letter",
        "CV / Lebenslauf",
        "Portfolio",
        "Are you authorised to work here?",
        "Will you need sponsorship?",
        "Why this team?",
        "Location",
        "Other attachment",
    ],
)
@pytest.mark.parametrize("mark", ["", " *", " (required)", " (optional)"])
def test_named_group_uses_one_accessible_label_and_preserves_file_value(
    label: str, mark: str
) -> None:
    root = base.parse_snapshot(
        f'- group "{label}{mark}" [ref=e1]:\n'
        f"  - generic [ref=e2]: {label}\n"
        '  - generic [ref=e3]: "*"\n'
        '  - button "Upload File" [ref=e4]\n'
        "  - generic [ref=e5]: cv.pdf\n"
    )
    found = base.discover_fields(root, Ashby.submit_names)
    assert len(found.fields) == 1
    field = found.fields[0]
    assert (field.label, field.kind, field.ref, field.value) == (label, "file", "e4", "cv.pdf")
    # A separate required marker remains meaningful when the accessible name omits it.
    assert field.required


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("named", [True, False])
def test_nearest_field_label_does_not_include_an_outer_group_or_sibling(
    depth: int, named: bool
) -> None:
    inner = '- button "Upload File" [ref=e9]\n'
    for _ in range(depth):
        inner = "- generic:\n" + "".join("  " + line + "\n" for line in inner.splitlines())
    group = '- group "Resume *":\n' if named else "- generic:\n"
    snapshot = (
        '- group "Application form":\n'
        '  - group "Unrelated":\n'
        '    - textbox "Do not use this label" [ref=e7]\n'
        "  " + group + "    - generic: Resume\n"
        '    - generic: "*"\n' + "".join("    " + line + "\n" for line in inner.splitlines())
    )
    found = base.discover_fields(base.parse_snapshot(snapshot), Ashby.submit_names)
    files = [f for f in found.fields if f.kind == "file"]
    assert len(files) == 1
    assert files[0].label == "Resume"
    assert files[0].required


@pytest.mark.parametrize(
    "label", ["Autofill from resume", "Auto-fill from résumé", "Remplissage automatique"]
)
def test_named_autofill_groups_remain_skipped(label: str) -> None:
    snapshot = f'- group "{label}":\n  - generic: {label}\n  - button "Upload File" [ref=e1]\n'
    found = base.discover_fields(base.parse_snapshot(snapshot), Ashby.submit_names)
    assert not found.fields
    assert [(s.label, s.reason) for s in found.skipped] == [(label, "autofill")]


@pytest.mark.parametrize(
    "label",
    [
        "Resume",
        "Résumé/CV",
        "Cover letter",
        "Portfolio",
        "Other attachment",
        "Location",
        "Why this team?",
        "Sponsorship",
        "Consent",
        "Application form",
    ],
)
@pytest.mark.parametrize("button", ["Preview", "Details", "Explain"])
def test_named_groups_do_not_promote_unknown_buttons_to_fields(label: str, button: str) -> None:
    snapshot = f'- group "{label}":\n  - generic: {label}\n  - button "{button}" [ref=e1]\n'
    found = base.discover_fields(base.parse_snapshot(snapshot), Ashby.submit_names)
    assert not found.fields
    assert not found.submit_refs
    assert [(s.label, s.reason) for s in found.skipped] == [(label, "unsupported_widget")]
