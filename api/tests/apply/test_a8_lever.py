"""The Lever adapter of A8 (tasks/apply/todo.md, "A8"): its addresses, its letter box and its
hCaptcha as accept and refuse tables, the fill on the modelled Lever form of a8_support (the
resume parse corrected against the profile, native selects, the letter in "Additional
information", hCaptcha for the person), and the whole path on a real Postgres: A5's fill, A6's
gate and submit lease. No browser, no model, no company: every submission counted here is a
call of the fake form's `gate_click`."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import urllib.request
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import gate, shots
from app.apply.answers import Answer, Context, Documents, Left, Posting
from app.apply.ats import adapter_for
from app.apply.ats import base as ats_base
from app.apply.ats.lever import Lever, letter_box
from app.apply.models import Application, ApplicationAnswer, ApplicationEvent, State
from app.browser.tools import LeaseTools
from app.jobs.models import Job
from app.profile import service as profile_service
from tests.apply import a1_support
from tests.apply.a4_support import Spec
from tests.apply.a5_support import FakeAgent, FakeLeases
from tests.apply.a6_support import GateLeases
from tests.apply.a8_support import (
    CAPTCHA_PATH,
    COMMENTS,
    HCAPTCHA,
    LEVER_PATH,
    PARSED,
    SPONSORSHIP,
    LeverForm,
    lever_form,
)
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, fill, services, started
from tests.apply.test_a6_gate import TTL, approve, run_submit, submit_services
from tests.profile.conftest import full

U = "0f3c2a1e-5b7d-4c8e-9a10-1b2c3d4e5f60"
FORM = f"https://jobs.lever.co/example-robotics/{U}/apply"
STUB = "http://127.0.0.1:8100"
LETTER = "Dear Example Robotics team,\n\nI build agents for operations. " + "Line. " * 120


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


# --- the tables ----------------------------------------------------------------------------------

ADDRESSES = [
    (f"https://jobs.lever.co/example-robotics/{U}", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}/apply", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}/", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}/apply/", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}?lever-source=LinkedIn", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}/apply?lever-origin=applied", FORM),
    (f"https://jobs.lever.co/example-robotics/{U}#top", FORM),
    (f"https://JOBS.LEVER.CO/example-robotics/{U}", FORM),
    (f"https://jobs.lever.co/example-robotics/{U.upper()}", FORM),
    (f"https://jobs.eu.lever.co/example-robotics/{U}", FORM.replace("jobs.", "jobs.eu.")),
    (f"https://jobs.lever.co/example.robotics/{U}", FORM.replace("example-", "example.")),
    (f"https://jobs.lever.co/example_robotics/{U}", FORM.replace("example-", "example_")),
    (f"https://jobs.lever.co/acme42/{U}", f"https://jobs.lever.co/acme42/{U}/apply"),
    (
        f"https://jobs.lever.co/ExampleRobotics/{U}",
        f"https://jobs.lever.co/ExampleRobotics/{U}/apply",
    ),
]
NOT_LEVER = [
    f"http://jobs.lever.co/example-robotics/{U}",
    f"ftp://jobs.lever.co/example-robotics/{U}",
    f"jobs.lever.co/example-robotics/{U}",
    f"https://lever.co/example-robotics/{U}",
    f"https://www.lever.co/example-robotics/{U}",
    f"https://hire.lever.co/example-robotics/{U}",
    f"https://jobs.lever.co.evil.example/example-robotics/{U}",
    f"https://evil-jobs.lever.co.example/example-robotics/{U}",
    f"https://jobs.lever.co:8443/example-robotics/{U}",
    f"https://jobs.lever.co:abc/example-robotics/{U}",
    f"https://someone@jobs.lever.co/example-robotics/{U}",
    "https://jobs.lever.co/example-robotics",
    "https://jobs.lever.co/",
    "https://jobs.lever.co",
    f"https://jobs.lever.co/example-robotics/{U}/apply/extra",
    f"https://jobs.lever.co/example-robotics/{U}/apply/apply",
    f"https://jobs.lever.co/example-robotics/{U}/thanks",
    f"https://jobs.lever.co/example-robotics/{U}/apply.html",
    "https://jobs.lever.co/example-robotics/not-a-uuid",
    f"https://jobs.lever.co/example-robotics/{U.replace('-', '')}",
    f"https://jobs.lever.co/example-robotics/{U[:-1]}",
    f"https://jobs.lever.co/example-robotics/{U[:-1]}g",
    f"https://jobs.lever.co/example-robotics/{U}x",
    f"https://jobs.lever.co/../{U}",
    f"https://jobs.lever.co/example%2Frobotics/{U}",
    f"https://jobs.lever.co//{U}",
    f"https://jobs.lever.co/example-robotics//{U}",
    f"https://jobs.lever.co/-example/{U}",
    f"https://jobs.lever.co/{'a' * 5000}/{U}",
    f"https://jobs.ashbyhq.com/example-robotics/{U}",
    "",
]
STUB_ADDRESSES = [
    (STUB + LEVER_PATH, STUB + LEVER_PATH),
    (STUB + LEVER_PATH.removesuffix("/apply.html"), STUB + LEVER_PATH),
    (STUB + LEVER_PATH.removesuffix("apply.html"), STUB + LEVER_PATH),
    (STUB + CAPTCHA_PATH, STUB + CAPTCHA_PATH),
    (STUB + "/lever/example-robotics/applied-ai-engineer", None),
    (STUB + "/forms/a8/lever/example-robotics", None),
    (STUB + "/forms/a8/lever/example-robotics/a/b/apply.html", None),
    (STUB + "/forms/a8/other/example-robotics/applied-ai-engineer/apply.html", None),
    (STUB + "/forms/a8/lever/example-robotics/APPLIED/apply.html", None),
    (STUB + "/forms/a8/lever/example-robotics/applied-ai-engineer/index.html", None),
    ("http://127.0.0.1:8101" + LEVER_PATH, None),
]


@pytest.mark.parametrize(("url", "expected"), ADDRESSES)
def test_a_lever_address_is_read_as_its_form(url: str, expected: str) -> None:
    assert Lever().form_url(url) == expected
    assert isinstance(adapter_for(url), Lever)


@pytest.mark.parametrize("url", NOT_LEVER)
def test_anything_else_is_not_lever(url: str) -> None:
    assert Lever().form_url(url) is None
    assert Lever(STUB).form_url(url) is None


@pytest.mark.parametrize(("url", "expected"), STUB_ADDRESSES)
def test_the_fake_lever_form_is_lever_only_under_the_stub(url: str, expected: str | None) -> None:
    assert Lever(STUB).form_url(url) == expected
    assert Lever().form_url(url) is None  # never without the stub setting
    if expected is not None:
        assert isinstance(adapter_for(url, stub_base=STUB), Lever)


LETTER_BOXES = [
    "Additional information",
    "ADDITIONAL INFORMATION",
    "Additional information *",
    "* Additional information",
    "Additional information (optional)",
    "Additional information:",
    "Additional Information.",
    "  Additional   information  ",
    COMMENTS,
    "add a cover letter or anything else you want to share",
    "Informations complémentaires",
    "Informations supplémentaires",
    "Additional information (required)",
    "Informations complémentaires (facultatif)",
    "Informations complémentaires :",
]
NOT_LETTER_BOXES = [
    "Additional information about your visa",
    "Any additional information about your salary expectations?",
    "Additional information required by law: your date of birth",
    "Additional informations about your nationality",
    "Additional information you do not want us to see",
    "Not additional information",
    "Additional questions",
    "Additional info",
    "Additional comments",
    "Information",
    "Informations",
    "Cover letter",
    "Add a cover letter",
    "Anything else you want to share?",
    "Anything else?",
    "Notes",
    "Comments",
    "LinkedIn URL",
    "Portfolio URL",
    "Additional information?",
    "Any additional information",
    "Additional details",
    "Other information",
    "Information additionnelle",
    "Informations complémentaires sur votre visa",
    "additional_information",
    "",
    "x" * 5000,
]


@pytest.mark.parametrize("label", LETTER_BOXES)
def test_the_letter_box_is_additional_information(label: str) -> None:
    assert letter_box(label)


@pytest.mark.parametrize("label", NOT_LETTER_BOXES)
def test_no_other_box_takes_the_letter(label: str) -> None:
    assert not letter_box(label)


CAPTCHAS = [
    f'- checkbox "{HCAPTCHA}" [ref=f1e3]',
    '- checkbox "hCaptcha" [ref=f1e3]',
    '- checkbox "HCAPTCHA CHECKBOX" [ref=f1e3]',
    '- button "hCaptcha challenge" [ref=e9]',
    '- iframe "hCaptcha security challenge" [ref=e9]',
    '- checkbox "hCaptcha checkbox. Sélectionnez pour lancer le défi." [ref=f1e3]',
    '- checkbox "I am human" [ref=f1e3]',
]
NOT_CAPTCHAS = [
    '- checkbox "I agree to hCaptcha\'s privacy policy" [ref=e3]',
    '- checkbox "Privacy (hCaptcha)" [ref=e3]',
    '- checkbox "hCaptchas are not used here" [ref=e3]',
    '- checkbox "notahcaptcha" [ref=e3]',
    '- link "hCaptcha" [ref=e3]',
    '- heading "hCaptcha" [level=2] [ref=e3]',
    '- textbox "hCaptcha" [ref=e3]',
    "- text: hCaptcha",
    '- checkbox "I accept the terms" [ref=e3]',
    "- iframe [ref=e3]",
]


@pytest.mark.parametrize("line", CAPTCHAS)
def test_hcaptcha_is_a_step_for_the_person(line: str) -> None:
    assert Lever().pending_step(line) == "captcha"


@pytest.mark.parametrize("line", NOT_CAPTCHAS)
def test_nothing_else_is_hcaptcha(line: str) -> None:
    assert Lever().pending_step(line) == ""


# --- the fill on the modelled form ---------------------------------------------------------------


def context(letter: str | None = LETTER, letter_path: str | None = None) -> Context:
    sections = {s: full(s) for s in ("identity", "experience", "skills", "answers")}
    return Context(
        uuid.uuid4(),
        sections,
        Posting.at("Paris, France", "Applied AI Engineer"),
        Documents("cv.pdf", letter_path, letter),
    )


def filled(form: LeverForm, ctx: Context | None = None) -> ats_base.Report:
    return asyncio.run(Lever(STUB).fill_form(form, ctx or context()))


def outcome(report: ats_base.Report, label: str) -> ats_base.Outcome:
    return next(o for o in report.outcomes if o.field.label == label)


@pytest.mark.parametrize("parse_after", [0, 1, 3, 5])
def test_the_resume_parse_is_corrected_against_the_profile(parse_after: int) -> None:
    form = lever_form(files=("cv.pdf",), parse_after=parse_after)
    report = filled(form)
    assert report.stopped == ""
    assert form.parses == 1
    profile = full("identity")
    assert form.spec("Full name").value == profile["full_name"]
    assert form.spec("Email").value == profile["email"]
    assert form.spec("Phone").value == profile["phone"]
    # The parse's guess for a question the profile does not answer is cleared, never kept.
    assert form.spec("Current company").value == ""
    assert outcome(report, "Current company").filled.status == "left"
    assert outcome(report, "Resume/CV").filled.status == "filled"
    order = [c[0] for c in form.calls]
    assert order.index("upload") < order.index("type")
    shown = {s.value for s in form.fields}
    assert not shown & set(PARSED.values())


def test_a_parse_later_than_the_waits_is_left_to_the_read_back() -> None:
    form = lever_form(files=("cv.pdf",), parse_after=40)
    report = filled(form)
    assert report.stopped == ""
    assert outcome(report, "Resume/CV").filled.status == "filled"
    assert sum(1 for c in form.calls if c[0] == "wait_for") >= 5


def test_native_selects_take_the_profile_answer() -> None:
    form = lever_form(files=("cv.pdf",))
    report = filled(form)
    assert form.spec(SPONSORSHIP).value == "No"
    assert outcome(report, SPONSORSHIP).filled.status == "filled"
    assert any(c[0] == "select_option" and c[1]["values"] == ["No"] for c in form.calls)
    # EEO stays the person's (decline by default, never inferred).
    assert form.spec("Gender").value == ""


def test_the_letter_goes_into_additional_information() -> None:
    form = lever_form(files=("cv.pdf",))
    report = filled(form)
    assert form.spec(COMMENTS).value == LETTER
    found = outcome(report, COMMENTS)
    assert isinstance(found.answer, Answer)
    assert found.answer.source == "documents.letter_text"
    assert found.filled.status == "filled"
    assert len(LETTER) > ats_base.NAME_MAX  # read back whole, not cut at a label's length


def test_without_a_letter_the_box_stays_for_the_agent() -> None:
    form = lever_form(files=("cv.pdf",))
    report = filled(form, context(letter=None))
    assert form.spec(COMMENTS).value == ""
    assert isinstance(outcome(report, COMMENTS).answer, Left)


def test_an_uploaded_letter_keeps_additional_information_free() -> None:
    form = lever_form(files=("cv.pdf", "letter.pdf"))
    form.fields.insert(1, Spec("Cover letter", "file"))
    report = filled(form, context(letter_path="letter.pdf"))
    assert form.spec("Cover letter").value == "letter.pdf"
    assert form.spec(COMMENTS).value == ""
    assert isinstance(outcome(report, COMMENTS).answer, Left)


def test_hcaptcha_needs_the_person_before_anything_is_typed() -> None:
    form = lever_form(files=("cv.pdf",), hcaptcha=True)
    report = filled(form)
    assert (report.stopped, report.needs_person) == ("needs_person", "captcha")
    assert form.acted() == []


def test_the_adapter_never_clicks_the_submit_and_the_agent_binding_has_no_gate() -> None:
    form = lever_form(files=("cv.pdf",))
    filled(form)
    assert form.submit_clicks == 0
    assert form.gate_clicks == []
    assert not hasattr(Lever, "gate_click")
    assert not hasattr(LeaseTools, "gate_click")


# --- the run, the gate and the submit lease ------------------------------------------------------


def on_lever(db: Session, person: Person, stub: str, path: str = LEVER_PATH) -> None:
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(url=stub + path))
    db.commit()


def ready(db: Session, factory: sessionmaker[Session], person: Person, stub: str) -> uuid.UUID:
    on_lever(db, person, stub)
    app_id = started(db, person)
    assert (
        fill(services(factory, lever_form(), None, FakeAgent(), stub_base=stub), app_id) == "ready"
    )
    return app_id


def answers_of(db: Session, app_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(
        sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id)
    ).scalars()
    return {r.label: r.value for r in rows}


def test_lever_is_filled_approved_and_submitted_once_through_the_gate(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    stored = answers_of(db, app_id)
    assert stored["Full name"] == full("identity")["full_name"]
    assert stored["Current company"] is None
    assert stored[SPONSORSHIP] == "No"
    assert isinstance(stored[COMMENTS], str)
    assert stored[COMMENTS].strip()
    # A letter longer than a label's cap, so the refill's read back of the whole text is tested.
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == COMMENTS)
        .values(value=LETTER)
    )
    db.commit()
    assert len(LETTER) > ats_base.NAME_MAX
    assert approve(factory, person_a, app_id).passed

    form = lever_form()
    assert run_submit(submit_services(factory, form, stub), app_id) == "submitted"
    assert application(db, app_id).state is State.submitted
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    assert form.submit_clicks == 0
    # the refill undid the parse again, and typed the approved letter whole
    assert form.parses == 1
    assert form.spec("Current company").value == ""
    assert form.spec(COMMENTS).value == LETTER


def test_a_lever_hcaptcha_needs_the_person_and_nothing_is_typed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    on_lever(db, person_a, stub, CAPTCHA_PATH)
    app_id = started(db, person_a)
    form = lever_form(hcaptcha=True)
    assert (
        fill(services(factory, form, None, FakeAgent(), stub_base=stub), app_id) == "needs_person"
    )
    found = application(db, app_id)
    assert found.needs == "captcha"
    assert [c for c in form.acted() if c[0] not in ("put_file", "screenshot")] == []


def test_a_never_apply_lever_posting_never_starts(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    on_lever(db, person_a, stub)
    app_id = started(db, person_a)
    profile = profile_service.get_profile(db, person_a.user_id, AT)
    search = {**full("search"), "never_apply": ["No security roles: Security Engineer"]}
    profile_service.save_section(db, person_a.user_id, "search", search, profile.revision, AT)
    db.execute(
        sa.update(Job).where(Job.id == person_a.posting_id).values(title="Security Engineer")
    )
    db.commit()
    leases, form = FakeLeases(), lever_form()
    assert fill(services(factory, form, leases, stub_base=stub), app_id) == "blocked"
    assert application(db, app_id).error_code == "never_apply_role"
    assert leases.acquired == []
    assert form.calls == []


def test_person_a_never_reads_or_approves_person_b_at_the_same_company(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_a = ready(db, factory, person_a, stub)
    app_b = ready(db, factory, person_b, stub)
    with factory() as s:
        found = s.get(Application, app_b)
        assert found is not None
        seen = gate.current_hash(s, found)
        with pytest.raises(gate.NotFoundError):
            gate.authorise_approval(s, person_a.user_id, app_b, seen, AT, TTL)
        s.rollback()
        auth_b = gate.authorise_approval(s, person_b.user_id, app_b, seen, AT, TTL)
        s.commit()
        with pytest.raises(gate.NotFoundError):
            gate.decide(s, person_a.user_id, auth_b.id, AT, switches=gate.Switches(True, False))
        s.rollback()
    assert shots.of_application(db, person_a.user_id, app_b) == []
    assert shots.of_application(db, person_b.user_id, app_b) != []
    assert application(db, app_a).state is State.ready
    assert application(db, app_b).state is State.ready


def test_no_answer_typed_on_lever_reaches_an_event_a_log_or_a_result(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "canary" + secrets.token_hex(8)
    caplog.set_level(logging.DEBUG)
    app_id = ready(db, factory, person_a, stub)
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == "Phone")
        .values(value=f"+33 6 00 {canary}")
    )
    db.commit()
    assert approve(factory, person_a, app_id).passed
    form, leases = lever_form(), GateLeases()
    result = run_submit(submit_services(factory, form, stub, leases=leases), app_id)
    assert result == "submitted"
    assert f"+33 6 00 {canary}" in [c[1].get("text") for c in form.calls if c[0] == "type"]
    stored = db.execute(
        sa.select(ApplicationEvent.data).where(ApplicationEvent.application_id == app_id)
    ).scalars()
    assert canary not in json.dumps(list(stored))
    assert canary not in caplog.text
    assert canary not in result
    assert canary not in json.dumps(leases.acquired)


# --- the fake Lever pages ------------------------------------------------------------------------


def test_the_fake_server_serves_the_lever_pages_and_records_nothing_on_a_read(
    forms: a1_support.Forms,
) -> None:
    for path, kind in (
        (LEVER_PATH, "text/html"),
        (CAPTCHA_PATH, "text/html"),
        ("/forms/a8/lever.js", "javascript"),
        ("/forms/a8/hcaptcha.html", "text/html"),
    ):
        with urllib.request.urlopen(forms.base + path, timeout=5) as answer:  # noqa: S310
            assert answer.status == 200
            assert kind in answer.headers["Content-Type"]
    assert forms.submissions() == []
