# ruff: noqa: E501, PT018, the accept and refuse tables read best one case to a line
"""Apply A4: the Greenhouse and Ashby adapters. Reading recorded snapshots of the fake ATS forms
(tests/ats_forms/a4/snapshots/, Playwright 1.55's AI snapshot of the A1 stub in Chrome), the
accept and refuse tables of every rule that reads a page or an address, and the fill through a
fake of the lease tools that is as hostile as browser B5's (tests/apply/a4_support.py)."""

import asyncio
import logging
import secrets
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.apply import answers
from app.apply.ats import ADAPTERS, adapter_for, base
from app.apply.ats.ashby import Ashby
from app.apply.ats.greenhouse import Greenhouse
from tests.apply.a4_support import FakeForm, Spec, ashby_form, greenhouse_form
from tests.profile.conftest import full

SNAPSHOTS = Path(__file__).resolve().parents[3] / "tests" / "ats_forms" / "a4" / "snapshots"
STUB = "http://ats-forms.test:8100"


def snap(name: str) -> str:
    return (SNAPSHOTS / f"{name}.txt").read_text()


def context(**identity: Any) -> answers.Context:
    sections = {s: full(s) for s in ("identity", "experience", "skills", "answers")}
    sections["identity"] = {**sections["identity"], "email": "e2e-a4@example.org", **identity}
    return answers.Context(
        uuid.uuid4(),
        sections,
        answers.Posting.at("Paris, France"),
        answers.Documents(cv_path="cv.pdf", letter_path="letter.pdf"),
    )


def fill(adapter: base.Adapter, form: FakeForm, ctx: answers.Context | None = None) -> base.Report:
    return asyncio.run(adapter.fill_form(form, ctx or context()))


def by_label(report: base.Report) -> dict[str, base.Outcome]:
    return {o.field.label: o for o in report.outcomes}


# --- reading recorded snapshots ----------------------------------------------------------------


def test_the_recorded_greenhouse_form_reads_field_for_field() -> None:
    found = base.discover_fields(base.parse_snapshot(snap("greenhouse")), Greenhouse.submit_names)
    kinds = [(f.label, f.kind, f.required) for f in found.fields]
    assert kinds[:8] == [
        ("First Name", "text", True),
        ("Last Name", "text", True),
        ("Email", "text", True),
        ("Phone", "text", False),
        ("Resume/CV", "file", True),
        ("Cover Letter", "file", False),
        (
            "Are you legally authorized to work in the country in which you are applying?",
            "combobox",
            True,
        ),
        ("Country", "combobox", True),
    ]
    assert [f.label for f in found.fields if f.kind == "checkbox"] == [
        "I agree to resolve any employment dispute through binding arbitration.",
        "I would like to receive news and marketing updates from Northwind Labs.",
        "I have read the Applicant Privacy Notice.",
    ]
    assert {"Gender", "Hispanic ethnicity", "Veteran status", "Disability status"} <= {
        f.label for f in found.fields
    }
    assert found.submit_refs == ["e92"] and not found.skipped and not found.alerts


def test_the_recorded_ashby_form_reads_yes_no_groups_by_their_buttons() -> None:
    found = base.discover_fields(base.parse_snapshot(snap("ashby")), Ashby.submit_names)
    pairs = [f for f in found.fields if f.kind == "yes_no"]
    assert [(f.label, dict(f.buttons)) for f in pairs] == [
        (
            "Are you legally authorised to work in the European Union?",
            {"Yes": "e32", "No": "e33"},
        ),
        ("Will you now or in the future require visa sponsorship?", {"Yes": "e38", "No": "e39"}),
    ]
    location = found.find("combobox:current location#1")
    assert location is not None and location.options == () and location.required


def test_values_on_the_page_are_read_back() -> None:
    chosen = base.discover_fields(
        base.parse_snapshot(snap("greenhouse-chosen")), Greenhouse.submit_names
    )
    values = {f.label: (f.value, f.checked) for f in chosen.fields}
    assert values["First Name"] == ("Alex", None)
    assert values["Resume/CV"] == ("cv.pdf", None)
    assert values["Country"] == ("France", None)
    assert values["Cover Letter"] == ("", None)
    assert values["How did you hear about this job?"] == ("", None)
    assert values["I have read the Applicant Privacy Notice."] == ("", True)
    opened = base.discover_fields(base.parse_snapshot(snap("ashby-open")), Ashby.submit_names)
    pair = opened.find("yes_no:are you legally authorised to work in the european union?#1")
    assert pair is not None and (pair.value, pair.checked) == ("Yes", True)
    assert opened.find("combobox:current location#1").value == "Par"  # type: ignore[union-attr]
    assert [o.name for o in base._options_outside_selects(opened.root or base.Node("x"))] == []


def test_the_framed_embed_is_recognised_and_read_with_frame_refs() -> None:
    page = snap("greenhouse-site")
    adapter = adapter_for(f"{STUB}/sites/northwind-labs/careers/4012345", page, stub_base=STUB)
    assert isinstance(adapter, Greenhouse)
    found = base.discover_fields(base.parse_snapshot(page), Greenhouse.submit_names)
    assert all(f.ref.startswith("f1e") for f in found.fields if f.ref)
    assert found.submit_refs == ["f1e89"]


@pytest.mark.parametrize(
    ("name", "adapter", "problem"),
    [
        ("greenhouse", Greenhouse(), ""),
        ("greenhouse-site", Greenhouse(), ""),
        ("ashby", Ashby(), ""),
        ("greenhouse-layout", Greenhouse(), "submit_missing"),
        ("ashby-layout", Ashby(), "submit_missing"),
        ("greenhouse", Ashby(), "anchor_missing"),
        ("ashby", Greenhouse(), "anchor_missing"),
        ("greenhouse-closed", Greenhouse(), "submit_missing"),
        ("greenhouse-confirm", Greenhouse(), "submit_missing"),
    ],
)
def test_anchors(name: str, adapter: base.Adapter, problem: str) -> None:
    found = base.discover_fields(base.parse_snapshot(snap(name)), adapter.submit_names)
    assert adapter.anchor_problem(found) == problem


def test_the_errors_after_a_refused_submit_are_the_alerts() -> None:
    assert Greenhouse().errors(snap("greenhouse-errors")) == [
        "Last Name is required",
        "Email is required",
        "Are you legally authorized to work in the country in which you are applying? is required",
        "Fix the highlighted fields.",
    ]
    assert Greenhouse().errors(snap("greenhouse")) == []


def test_the_parser_is_one_pass_and_bounded() -> None:
    line = '  - textbox "Q *" [ref=e{}]: v\n'
    text = "- generic [ref=e0]:\n" + "".join(line.format(i) for i in range(20_000))
    root = base.parse_snapshot(text)
    assert len(root.children[0].children) == 20_000
    long = '- textbox "' + "x" * 50_000 + '" [ref=e1]'
    assert len(base.parse_snapshot(long).children[0].name) == base.NAME_MAX


@pytest.mark.parametrize(
    ("line", "role", "name", "text", "attrs"),
    [
        ('- textbox "Email *" [ref=e13]', "textbox", "Email *", "", {}),
        ('- textbox "Email" [ref=e13]: a@b.c', "textbox", "Email", "a@b.c", {}),
        ('- generic [ref=e8]: "*"', "generic", "", "*", {}),
        ('- button "Yes" [pressed] [ref=e34]', "button", "Yes", "", {"pressed": ""}),
        ('- heading "H" [level=1] [ref=e2]', "heading", "H", "", {"level": "1"}),
        ('- checkbox "Say \\"hi\\"" [checked] [ref=e1]', "checkbox", 'Say "hi"', "", {"checked": ""}),
        ("- text: Full name", "text", "", "Full name", {}),
        ('- option "Paris, France" [ref=e22] [cursor=pointer]', "option", "Paris, France", "", {"cursor": "pointer"}),
    ],
)  # fmt: skip
def test_snapshot_lines(line: str, role: str, name: str, text: str, attrs: dict[str, str]) -> None:
    node = base.parse_snapshot(line).children[0]
    assert (node.role, node.name, node.text, node.attrs) == (role, name, text, attrs)


def test_lines_it_does_not_read_are_skipped() -> None:
    text = '- link "x" [ref=e1]:\n  - /url: https://example.org\n[snapshot cut at 40000]\n'
    root = base.parse_snapshot(text)
    assert [n.role for n in root.walk()] == ["root", "link"]


# --- labels, yes or no groups, file buttons: accept and refuse --------------------------------


@pytest.mark.parametrize(
    ("name", "label", "required"),
    [
        ("Email *", "Email", True),
        ("Email*", "Email", True),
        ("Email ✱", "Email", True),
        ("Email (required)", "Email", True),
        ("Email (Required) *", "Email", True),
        ("E-mail (obligatoire)", "E-mail", True),
        ("Phone (optional)", "Phone", False),
        ("Téléphone (facultatif)", "Téléphone", False),
        ("Phone", "Phone", False),
        ("*", "", True),
        ("", "", False),
        ("Rate us * 5", "Rate us * 5", False),  # a star inside the label is no mark
        ("Required skills", "Required skills", False),
        ("Optional: portfolio", "Optional: portfolio", False),
        ("Email (required) (optional)", "Email", True),
    ],
)
def test_label_marks(name: str, label: str, required: bool) -> None:
    assert base.label_of(name) == (label, required)


def _pair(*buttons: str, extra: str = "") -> str:
    lines = [
        "- generic [ref=e1]:",
        "  - generic [ref=e2]: Do you need a visa?",
        "  - generic [ref=e3]:",
    ]
    lines += [f'    - button "{b}" [ref=e{10 + i}]' for i, b in enumerate(buttons)]
    if extra:
        lines.append(f"    {extra}")
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("buttons", "extra", "is_pair"),
    [
        (("Yes", "No"), "", True),
        (("No", "Yes"), "", True),
        (("YES", "no"), "", True),
        (("Oui", "Non"), "", True),
        (("Non", "Oui"), "", True),
        (("Yes", "No", "Maybe"), "", False),
        (("Yes",), "", False),
        (("Yes", "Yes"), "", False),
        (("Yes please", "No"), "", False),
        (("Yes", "No thanks"), "", False),
        (("Oui", "No"), "", False),
        (("Y", "N"), "", False),
        (("True", "False"), "", False),
        (("Yes", "No"), "- generic [ref=e30]: hint", False),
        (("Yes", "No"), '- textbox "Other" [ref=e31]', False),
    ],
)
def test_yes_no_groups_are_exactly_two_buttons(
    buttons: tuple[str, ...], extra: str, is_pair: bool
) -> None:
    found = base.discover_fields(base.parse_snapshot(_pair(*buttons, extra=extra)), frozenset())
    pairs = [f for f in found.fields if f.kind == "yes_no"]
    assert bool(pairs) is is_pair
    if is_pair:
        assert pairs[0].label == "Do you need a visa?" and set(pairs[0].buttons) == {"Yes", "No"}
    # A button that is not part of a pair is never a field to click.
    assert not [f for f in found.fields if f.kind != "yes_no" and f.ref.startswith("e1")]


def _group(label: str, button: str, after: str = "") -> str:
    lines = [
        "- generic [ref=e1]:",
        f"  - generic [ref=e2]: {label}",
        f'  - button "{button}" [ref=e3]',
    ]
    if after:
        lines.append(f"  - generic [ref=e4]: {after}")
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("label", "button", "kind_label"),
    [
        ("Resume", "Resume", "Resume"),
        ("Resume/CV", "Resume/CV *", "Resume/CV"),
        ("Cover letter", "Cover letter", "Cover letter"),
        ("Resume", "Upload File", "Resume"),
        ("Resume", "upload file", "Resume"),
        ("CV", "Joindre un fichier", "CV"),
        ("Lettre de motivation", "Parcourir", "Lettre de motivation"),
        ("Resume", "Attach", "Resume"),
        ("Resume", "Remove", None),
        ("Resume", "Upload your resume to autofill", None),
        ("Resume", "Enter manually", None),
        ("Portfolio", "Add another", None),
        ("Education", "Add education", None),
        ("Resume", "Submit application", None),
        ("Autofill from resume", "Autofill from resume", None),
        ("Autofill from resume", "Upload File", None),
    ],
)
def test_file_fields(label: str, button: str, kind_label: str | None) -> None:
    found = base.discover_fields(
        base.parse_snapshot(_group(label, button)), frozenset({"submit application"})
    )
    files = [f.label for f in found.fields if f.kind == "file"]
    assert files == ([kind_label] if kind_label else [])


def test_a_chosen_file_name_follows_the_button_and_is_not_the_label() -> None:
    found = base.discover_fields(
        base.parse_snapshot(_group("Resume", "Resume", "cv.pdf")), frozenset()
    )
    assert [(f.label, f.value) for f in found.fields] == [("Resume", "cv.pdf")]


def test_unsupported_widgets_are_reported_never_filled() -> None:
    text = "\n".join(
        [
            "- generic [ref=e1]:",
            '  - radiogroup "Seniority" [ref=e2]:',
            '    - radio "Junior" [ref=e3]',
            '  - slider "Years" [ref=e4]',
            '  - switch "Relocate" [ref=e5]',
        ]
    )
    found = base.discover_fields(base.parse_snapshot(text), frozenset())
    assert found.fields == []
    assert {(s.role, s.reason) for s in found.skipped} >= {
        ("radiogroup", "unsupported_widget"),
        ("slider", "unsupported_widget"),
        ("switch", "unsupported_widget"),
    }


# --- addresses: accept and refuse -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "form"),
    [
        ("https://jobs.ashbyhq.com/acme/1b2c", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://jobs.ashbyhq.com/acme/1b2c/", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://jobs.ashbyhq.com/acme/1b2c/application", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://jobs.ashbyhq.com/acme/1b2c?utm_source=x", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://jobs.ashbyhq.com/acme/1b2c#apply", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://JOBS.ASHBYHQ.COM/acme/1b2c", "https://jobs.ashbyhq.com/acme/1b2c/application"),
        ("https://jobs.ashbyhq.com/Example.Co/a-b_c", "https://jobs.ashbyhq.com/Example.Co/a-b_c/application"),
        (f"{STUB}/ashby/example-robotics/applied-ai-engineer", f"{STUB}/ashby/example-robotics/applied-ai-engineer/application"),
        (f"{STUB}/ashby/example-robotics/applied-ai-engineer--closed/application", f"{STUB}/ashby/example-robotics/applied-ai-engineer--closed/application"),
        ("http://jobs.ashbyhq.com/acme/1b2c", None),
        ("https://jobs.ashbyhq.com/acme", None),
        ("https://jobs.ashbyhq.com/", None),
        ("https://jobs.ashbyhq.com/acme/1b2c/extra", None),
        ("https://jobs.ashbyhq.com/acme/1b2c/application/extra", None),
        ("https://jobs.ashbyhq.com.evil.example/acme/1b2c", None),
        ("https://evil.example/jobs.ashbyhq.com/acme/1b2c", None),
        ("https://user@jobs.ashbyhq.com/acme/1b2c", None),
        ("https://jobs.ashbyhq.com:8443/acme/1b2c", None),
        ("https://ashbyhq.com/acme/1b2c", None),
        ("https://jobs.ashbyhq.co/acme/1b2c", None),
        ("https://jobs.ashbyhq.com/acme/../1b2c", None),
        ("https://jobs.ashbyhq.com/acme/<script>", None),
        ("https://job-boards.greenhouse.io/acme/jobs/123", None),
        (f"{STUB}/greenhouse/northwind-labs/jobs/4012345", None),
        (f"{STUB}/ashby/only-org", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)  # fmt: skip
def test_ashby_addresses(url: str, form: str | None) -> None:
    assert Ashby(STUB).form_url(url) == form


@pytest.mark.parametrize(
    ("url", "form"),
    [
        ("https://job-boards.greenhouse.io/acme/jobs/4012345", "https://job-boards.greenhouse.io/acme/jobs/4012345"),
        ("https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x", "https://boards.greenhouse.io/acme/jobs/4012345"),
        ("https://job-boards.eu.greenhouse.io/acme/jobs/4012345/", "https://job-boards.eu.greenhouse.io/acme/jobs/4012345"),
        ("https://boards.eu.greenhouse.io/acme/jobs/4012345", "https://boards.eu.greenhouse.io/acme/jobs/4012345"),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345", "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345"),
        ("https://boards.greenhouse.io/embed/job_app?token=4012345&for=acme&b=1", "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345"),
        (f"{STUB}/greenhouse/northwind-labs/jobs/4012345", f"{STUB}/greenhouse/northwind-labs/jobs/4012345"),
        (f"{STUB}/greenhouse/northwind-labs/jobs/4012345--layout", f"{STUB}/greenhouse/northwind-labs/jobs/4012345--layout"),
        (f"{STUB}/greenhouse/embed/job_app?for=northwind-labs&token=4012345", f"{STUB}/greenhouse/embed/job_app?for=northwind-labs&token=4012345"),
        ("https://job-boards.greenhouse.io/acme/jobs/4012345--layout", None),
        ("https://job-boards.greenhouse.io/acme/jobs/abc", None),
        ("https://job-boards.greenhouse.io/acme/jobs/", None),
        ("https://job-boards.greenhouse.io/acme", None),
        ("https://job-boards.greenhouse.io/acme/jobs/123/extra", None),
        ("https://job-boards.greenhouse.io/acme/job/123", None),
        ("https://job-boards.greenhouse.io/embed/jobs/123", None),
        ("https://boards.greenhouse.io/embed/job_app?for=acme", None),
        ("https://boards.greenhouse.io/embed/job_app?token=123", None),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=12a", None),
        ("http://job-boards.greenhouse.io/acme/jobs/123", None),
        ("https://greenhouse.io/acme/jobs/123", None),
        ("https://job-boards.greenhouse.io.evil.example/acme/jobs/123", None),
        ("https://evil.example/?u=job-boards.greenhouse.io/acme/jobs/123", None),
        ("https://user@job-boards.greenhouse.io/acme/jobs/123", None),
        ("https://job-boards.greenhouse.io:444/acme/jobs/123", None),
        ("https://www.northwind.example/careers?gh_jid=4012345", None),
        ("https://jobs.ashbyhq.com/acme/1b2c", None),
        (f"{STUB}/ashby/example-robotics/applied-ai-engineer", None),
        (f"{STUB}/sites/northwind-labs/careers/4012345", None),
        ("", None),
    ],
)  # fmt: skip
def test_greenhouse_addresses(url: str, form: str | None) -> None:
    assert Greenhouse(STUB).form_url(url) == form


def test_without_a_stub_base_the_stub_addresses_are_nobodys() -> None:
    for url in (
        f"{STUB}/ashby/example-robotics/applied-ai-engineer",
        f"{STUB}/greenhouse/northwind-labs/jobs/4012345",
    ):
        assert adapter_for(url) is None
    assert [cls.name for cls in ADAPTERS] == ["ashby", "greenhouse"]


def test_an_unknown_page_has_no_adapter() -> None:
    page = '- generic [ref=e1]:\n  - textbox "Email" [ref=e2]\n  - button "Submit" [ref=e3]'
    assert adapter_for("https://careers.example.org/job/1", page) is None


# --- page sentences: accept and refuse --------------------------------------------------------

CONFIRMATIONS = [
    "Thank you for applying.",
    "Thank you for applying to Northwind Labs!",
    "Thanks for applying",
    "Thank you so much for applying.",
    "Thank you for your application.",
    "Thanks, for your application!",
    "Thank you for submitting your application.",
    "Your application was successfully submitted. Thank you!",
    "Your application has been submitted.",
    "Your application has been received",
    "Application submitted",
    "Application received!",
    "Application submitted successfully.",
    "We have received your application.",
    "We’ve received your application.",
    "Votre candidature a bien été envoyée.",
    "Votre candidature a été reçue",
    "Merci pour votre candidature !",
    "Merci de votre candidature.",
    "Candidature envoyée",
    "Application received\nWe will be in touch.",
    "THANK YOU FOR APPLYING",
]
NOT_CONFIRMATIONS = [
    "",
    "Thank you!",
    "Thank you for your interest.",
    "Thank you for your interest. This job is no longer open.",
    "Thank you for applying. Your application could not be submitted.",
    "Your application was not submitted.",
    "Your application has not been received.",
    "Application not submitted",
    "Fix the highlighted fields. Application submitted",
    "Email is required",
    "Something went wrong, please try again.",
    "Unfortunately, your application was not sent.",
    "Submit application",
    "Submit your application",
    "Before you submit your application, check your details.",
    "Your application",
    "Applications submitted after the deadline are not reviewed.",
    "Votre candidature n'a pas été envoyée.",
    "Erreur : votre candidature",
    "Merci",
    "Application submitted by recruiters on your behalf will be reviewed",
    "The job you are looking for is no longer open.",
    "Job not found",
]


@pytest.mark.parametrize("text", CONFIRMATIONS)
def test_confirmations(text: str) -> None:
    assert base.confirmed(text) and Greenhouse().confirmation(text) and Ashby().confirmation(text)


@pytest.mark.parametrize("text", NOT_CONFIRMATIONS)
def test_not_confirmations(text: str) -> None:
    assert not base.confirmed(text)


def test_the_recorded_confirmation_pages() -> None:
    for name in ("ashby-confirm", "greenhouse-confirm"):
        assert base.confirmed(base.page_text(snap(name)))
    for name in ("ashby", "greenhouse", "greenhouse-errors", "ashby-closed", "greenhouse-closed"):
        assert not base.confirmed(base.page_text(snap(name)))


@pytest.mark.parametrize(
    ("text", "is_closed"),
    [
        ("Job not found", True),
        ("The job you requested was not found.", True),
        ("Page not found", True),
        ("The job you are looking for is no longer open.", True),
        ("This position has been filled", True),
        ("This job is closed.", True),
        ("This posting is no longer accepting applications", True),
        ("Cette offre n'est plus disponible.", True),
        ("Apply for this job", False),
        ("We found no jobs matching your search", False),
        ("Not found what you need? Contact us", False),
        ("Job not found? Browse all openings and apply", False),
        ("Positions found: 3", False),
        ("The job requires a closed-loop control background", False),
        ("Applications are no longer reviewed on weekends", False),
        ("", False),
    ],
)
def test_closed_pages(text: str, is_closed: bool) -> None:
    assert base.closed(text) is is_closed


@pytest.mark.parametrize(
    ("snapshot", "step"),
    [
        ('- button "I am not a robot" [ref=e1]', "captcha"),
        ('- checkbox "I\'m not a robot" [ref=e1]', "captcha"),
        ('- iframe "reCAPTCHA" [ref=e1]', "captcha"),
        ('- iframe "hCaptcha security challenge" [ref=e1]', "captcha"),
        ('- button "Robots welcome" [ref=e1]', ""),
        ("- paragraph [ref=e1]: This site is protected by reCAPTCHA", ""),
        ('- textbox "Security code" [ref=e1]', "email_code"),
        ('- textbox "Verification code *" [ref=e1]', "email_code"),
        ('- textbox "Promo code" [ref=e1]', ""),
        ('- textbox "Postal code" [ref=e1]', ""),
        ('- checkbox "Security code" [ref=e1]', ""),
    ],
)
def test_pending_steps(snapshot: str, step: str) -> None:
    assert Greenhouse().pending_step(snapshot) == step
    assert Ashby().pending_step(snapshot) == (step if step == "captcha" else "")


# --- the fill ----------------------------------------------------------------------------------


def test_greenhouse_fills_every_kind_and_leaves_what_is_the_persons() -> None:
    form = greenhouse_form()
    report = fill(Greenhouse(), form)
    assert report.stopped == "" and report.needs_person == ""
    seen = by_label(report)
    assert form.spec("First Name").value == "Ada"
    assert form.spec("Last Name").value == "Example"
    assert form.spec("Email").value == "e2e-a4@example.org"
    assert form.spec("Resume/CV").value == "cv.pdf"
    assert form.spec("Cover Letter").value == "letter.pdf"
    assert form.spec("Country").value == "France"  # typed, then the option clicked
    assert seen["Country"].filled.status == "filled"
    assert seen["Country"].field.options == ("France", "Germany", "United Kingdom")
    # Privacy ticked; arbitration and marketing are the person's and stay unticked.
    assert form.spec("I have read the Applicant Privacy Notice.").checked
    for label in (
        "I agree to resolve any employment dispute through binding arbitration.",
        "I would like to receive news and marketing updates from Northwind Labs.",
    ):
        assert not form.spec(label).checked
        assert seen[label].filled == base.Filled("left", "person_choice")
    # EEO: the person's policy, nothing chosen; "how did you hear" has no option of the profile.
    assert form.spec("Gender").value == ""
    assert seen["Gender"].filled.status == "left"
    assert all(o.filled.status in ("filled", "left") for o in report.outcomes)


def test_ashby_fills_yes_no_by_the_buttons_and_the_typeahead_by_its_option() -> None:
    form = ashby_form()
    form.spec("Current location").options += ("Bordeaux, France", "Bordeaux, France (Gironde)")
    report = fill(Ashby(), form)  # the profile's city and country: "Bordeaux, France"
    seen = by_label(report)
    sponsor = "Will you now or in the future require visa sponsorship?"
    assert form.spec(sponsor).value == "No" and seen[sponsor].filled.status == "filled"
    assert form.spec("Current location").value == "Bordeaux, France"
    assert seen["Current location"].filled.status == "filled"
    assert form.spec("Why Example Robotics?").value == ""  # free text: the agent's (A5)
    assert seen["Why Example Robotics?"].filled == base.Filled("left", "free_text_for_the_agent")
    assert form.spec("I agree to the processing of my data for this application.").checked


def test_a_typeahead_whose_options_load_late_is_waited_for() -> None:
    form = ashby_form()
    form.spec("Current location").options += ("Bordeaux, France",)
    form.spec("Current location").load_after = 3
    report = fill(Ashby(), form)
    assert by_label(report)["Current location"].filled.status == "filled"
    assert sum(1 for c in form.calls if c[0] == "wait_for") >= 3


def test_a_typeahead_without_the_exact_option_is_left_and_cleared() -> None:
    form = ashby_form()  # "Bordeaux, France" is typed; the menu shows only longer names
    form.spec("Current location").options = (
        "Bordeaux, France (Gironde)",
        "Le Bordeaux, France",
    )
    report = fill(Ashby(), form)
    assert by_label(report)["Current location"].filled == base.Filled("left", "option_missing")
    spec = form.spec("Current location")
    assert spec.value == "" and spec.typed == ""  # nothing chosen, the typed text cleared


def test_an_option_that_appears_only_after_typing_is_clicked() -> None:
    form = greenhouse_form()
    form.spec("Country").options = ("France", "French Polynesia", "Germany")
    report = fill(Greenhouse(), form)
    assert form.spec("Country").value == "France"
    assert by_label(report)["Country"].filled.status == "filled"
    typed = [c[1]["text"] for c in form.calls if c[0] == "type" and c[1]["text"] == "France"]
    assert typed  # the answer is typed first, then its option clicked


def test_a_yes_no_group_without_a_pressed_state_is_unverified_not_filled() -> None:
    form = ashby_form()
    form.spec("Will you now or in the future require visa sponsorship?").pressed_state = False
    report = fill(Ashby(), form)
    outcome = by_label(report)["Will you now or in the future require visa sponsorship?"]
    assert outcome.filled == base.Filled("unverified", "no_pressed_state")


def test_a_field_named_only_by_aria_label_is_filled() -> None:
    form = greenhouse_form()
    form.fields.insert(4, Spec("LinkedIn Profile URL", "text", aria_only=True))
    form.fields = [f for f in form.fields if f.label != "LinkedIn Profile"]
    fill(Greenhouse(), form)
    assert form.spec("LinkedIn Profile URL").value.startswith("https://")


def test_an_upload_the_form_rejects_is_reported() -> None:
    form = greenhouse_form()
    form.spec("Resume/CV").reject_upload = True
    report = fill(Greenhouse(), form)
    assert by_label(report)["Resume/CV"].filled == base.Filled("failed", "upload_rejected")


def test_a_document_not_given_to_the_lease_is_refused_and_reported() -> None:
    form = greenhouse_form(files=("cv.pdf",))
    report = fill(Greenhouse(), form)
    assert by_label(report)["Cover Letter"].filled == base.Filled("failed", "upload_refused")


def test_the_changed_layout_stops_before_anything_is_typed() -> None:
    form = greenhouse_form(submit="Send application")
    report = fill(Greenhouse(), form)
    assert report.stopped == "form_changed" and report.outcomes == []
    assert form.acted() == []
    form = ashby_form()
    form.fields = [f for f in form.fields if f.label != "Resume"]
    report = fill(Ashby(), form)
    assert report.stopped == "form_changed" and form.acted() == []


def test_a_field_gone_mid_fill_stops_the_fill_and_nothing_more_is_typed() -> None:
    def drop_email(form: FakeForm, n: int) -> None:
        if any(c[0] == "type" and c[1]["text"] == "Example" for c in form.calls):
            form.fields = [f for f in form.fields if f.label != "Email"]

    form = greenhouse_form(on_call=drop_email)
    report = fill(Greenhouse(), form)
    assert report.stopped == "form_changed"
    assert report.outcomes[-1].field.label == "Email"
    assert report.outcomes[-1].filled == base.Filled("failed", "form_changed")
    typed_after = form.acted()[[c[1].get("text") for c in form.acted()].index("Example") + 1 :]
    assert typed_after == []


def test_a_cut_snapshot_stops_before_anything_is_typed() -> None:
    form = greenhouse_form(cut=True)
    report = fill(Greenhouse(), form)
    assert report.stopped == "snapshot_cut" and form.acted() == []


def test_the_adapter_never_clicks_the_submit_element_nor_presses_enter() -> None:
    for adapter, form in ((Greenhouse(), greenhouse_form()), (Ashby(), ashby_form())):
        fill(adapter, form)
        submits = {ref for ref, (what, _, _) in form._refs.items() if what == "submit"}
        assert form.calls  # it did work
        assert form.submit_clicks == 0
        for name, args in form.calls:
            assert not (name == "type" and args.get("submit"))
            assert not (name == "press_key" and args.get("key") in ("Enter", "Space"))
            assert args.get("ref") not in submits
        clicked = [args.get("element", "") for name, args in form.calls if name == "click"]
        assert not [e for e in clicked if "submit" in e.lower()]
        assert not hasattr(adapter, "gate_click") and not hasattr(adapter, "submit")


def test_the_framed_form_is_filled_through_frame_refs() -> None:
    form = greenhouse_form(frame=True)
    report = fill(Greenhouse(), form)
    assert report.stopped == "" and form.spec("Email").value == "e2e-a4@example.org"
    refs = [c[1]["ref"] for c in form.acted() if "ref" in c[1]]
    assert refs and all(r.startswith("f1e") for r in refs)


def test_ashby_falls_back_to_the_embed_when_the_hosted_page_is_not_found() -> None:
    hosted = "https://jobs.ashbyhq.com/acme/1b2c/application"
    form = ashby_form(pages={hosted: "Job not found"})
    code = asyncio.run(Ashby().open(form, "https://jobs.ashbyhq.com/acme/1b2c"))
    assert code == "" and form.url == hosted + "?embed=js"
    closed_both = ashby_form(pages={hosted: "Job not found", hosted + "?embed=js": "Job not found"})
    assert asyncio.run(Ashby().open(closed_both, hosted)) == "posting_closed"
    gone = "https://job-boards.greenhouse.io/acme/jobs/1"
    closed_gh = greenhouse_form(pages={gone: "The job you are looking for is no longer open."})
    assert asyncio.run(Greenhouse().open(closed_gh, gone)) == "posting_closed"


def test_a_left_answer_is_never_typed() -> None:
    form = greenhouse_form()
    report = fill(Greenhouse(), form, context(full_name=None, phone=None))
    seen = by_label(report)
    assert seen["First Name"].filled.status == "left" and form.spec("First Name").value == ""
    assert seen["Phone"].filled.status == "left" and form.spec("Phone").value == ""


def test_person_b_fills_with_b_s_values_only() -> None:
    a, b = greenhouse_form(), greenhouse_form()
    fill(Greenhouse(), a, context(full_name="Alex Sample", email="e2e-a@example.org"))
    fill(Greenhouse(), b, context(full_name="Blake Other", email="e2e-b@example.org"))
    typed_b = " ".join(str(c[1].get("text", "")) for c in b.calls)
    assert "Blake" in typed_b and "Alex" not in typed_b and "e2e-a@" not in typed_b


def test_no_value_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    canary = "Zq" + secrets.token_hex(8)
    caplog.set_level(logging.DEBUG)
    fill(Greenhouse(), greenhouse_form(), context(full_name=f"{canary} Example"))
    fill(Greenhouse(), greenhouse_form(submit="Other"), context(full_name=f"{canary} Example"))
    assert canary not in caplog.text
    assert "form_changed" in caplog.text


def test_a_question_the_form_adds_while_filling_is_filled_on_a_second_pass() -> None:
    def morph(form: FakeForm, n: int) -> None:
        if form.spec("First Name").value and not any(
            s.label == "Phone number" for s in form.fields
        ):
            form.fields.insert(2, Spec("Phone number", "text", True))

    form = greenhouse_form(on_call=morph)
    report = fill(Greenhouse(), form)
    assert form.spec("Phone number").value != ""
    assert by_label(report)["Phone number"].filled.status == "filled"


@pytest.mark.parametrize(
    ("buttons", "problem"),
    [
        (["Submit application"], ""),
        (["SUBMIT APPLICATION"], ""),
        (["Submit  application"], ""),
        (["Envoyer la candidature"], ""),
        (["Submit"], "submit_missing"),
        (["Send application"], "submit_missing"),
        (["Submit application now"], "submit_missing"),
        (["Submit your application"], "submit_missing"),
        (["Apply"], "submit_missing"),
        (["Submit applications"], "submit_missing"),
        ([], "submit_missing"),
        (["Submit application", "Submit application"], "submit_ambiguous"),
    ],
)
def test_the_submit_anchor(buttons: list[str], problem: str) -> None:
    form = greenhouse_form()
    lines = form.render().splitlines()[:-1]
    lines += [f'    - button "{b}" [ref=e9{i}]' for i, b in enumerate(buttons)]
    found = base.discover_fields(base.parse_snapshot("\n".join(lines)), Greenhouse.submit_names)
    assert Greenhouse().anchor_problem(found) == problem
