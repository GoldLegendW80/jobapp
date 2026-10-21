"""The Lever adapter of A8 (tasks/apply/todo.md, "A8"): its addresses, its letter box and its
hCaptcha as accept and refuse tables, the fill on the modelled Lever form of a8_support (the
resume parse corrected against the profile, native selects, the letter in "Additional
information", hCaptcha for the person), and the whole path on a real Postgres: A5's fill, A6's
gate and submit lease. No browser, no model, no company: every submission counted here is a
call of the fake form's `gate_click`."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import secrets
import string
import urllib.request
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import redis as redis_client
import sqlalchemy as sa
from arq.constants import job_key_prefix
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.apply import gate, shots
from app.apply.answers import Answer, Context, Documents, Left, Posting
from app.apply.ats import adapter_for
from app.apply.ats import base as ats_base
from app.apply.ats.lever import Lever, letter_box
from app.apply.bank import question_key
from app.apply.models import (
    AnswerBankEntry,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    State,
)
from app.browser.tools import LeaseTools
from app.config import Settings
from app.documents.models import DocumentVersion
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
    HEARD,
    LEVER_PATH,
    PARSED,
    SPONSORSHIP,
    LeverForm,
    lever_form,
)
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, fill, services, started
from tests.apply.test_a6_gate import (
    TTL,
    _password,
    api_app,
    approve,
    run_submit,
    submit_services,
)
from tests.conftest import make_settings
from tests.identity.api_helpers import Api
from tests.jobs.test_worker import flush_redis, redis_url
from tests.mail import canary as canaries
from tests.profile.conftest import full

__all__ = ["api_app", "redis_url"]

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


# The labels each render of the parse writes, one snapshot apart.
RENDERS = {
    "company_last": (("Full name", "Email", "Phone"), ("Current company",)),
    "three_renders": (("Full name",), ("Email",), ("Phone", "Current company")),
    "one_field_each": tuple((label,) for label in PARSED),
    "company_first": (("Current company",), ("Full name", "Email", "Phone")),
    "company_in_the_first": (("Full name", "Current company"), ("Email",), ("Phone",)),
}


@pytest.mark.parametrize("parse_after", [0, 1, 3])
@pytest.mark.parametrize("renders", RENDERS.values(), ids=RENDERS.keys())
def test_a_parse_written_over_several_renders_is_undone_whole(
    renders: tuple[tuple[str, ...], ...], parse_after: int
) -> None:
    form = lever_form(files=("cv.pdf",), parse_after=parse_after, renders=renders)
    report = filled(form)
    assert report.stopped == ""
    assert form.parses == 1
    assert sorted(form.cleared) == sorted(PARSED)
    assert form.spec("Current company").value == ""
    assert outcome(report, "Current company").filled.status == "left"
    assert form.spec("Full name").value == full("identity")["full_name"]
    assert not {s.value for s in form.fields} & set(PARSED.values())


def test_a_render_after_two_reads_agreed_is_left_to_the_read_back(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    # The second render lands after two reads agreed, past what the undo waits for.
    form = lever_form(renders=RENDERS["company_last"], gap=3)
    assert run_submit(submit_services(factory, form, stub), app_id) == "ready"
    assert application(db, app_id).error_code == "form_changed"
    assert form.spec("Current company").value == PARSED["Current company"]
    assert form.gate_clicks == []
    assert form.submit_clicks == 0


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


@pytest.mark.parametrize(
    ("role", "kept"),
    [
        *((r, ats_base.TEXT_MAX) for r in ("textbox", "searchbox", "spinbutton", "combobox")),
        *(
            (r, ats_base.NAME_MAX)
            for r in ("paragraph", "generic", "alert", "heading", "option", "button", "checkbox")
        ),
    ],
)
def test_only_the_text_of_a_value_is_kept_past_a_labels_length(role: str, kept: int) -> None:
    node = ats_base.parse_snapshot(f"- {role} [ref=e1]: " + "x" * 20_000).children[0]
    assert len(node.text) == kept


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


PASSIVE = ("snapshot", "wait_for", "navigate", "put_file", "screenshot")


def step_after_parse(step: str, after: int, **rest: Any) -> tuple[LeverForm, list[int]]:
    """A Lever form where hCaptcha or a sign in wall shows on the `after`th snapshot following the
    one the parse wrote in; the list receives that snapshot's index in `calls`."""
    shown: list[int] = []
    seen = [0]

    def show(form: Any, n: int) -> None:
        if shown or form.parses == 0 or form.calls[-1][0] != "snapshot":
            return
        seen[0] += 1
        if seen[0] == after + 1:
            shown.append(n - 1)
            if step == "captcha":
                form.hcaptcha = True
            else:
                form.fields.insert(0, Spec("Password", "text", True))

    return lever_form(on_call=show, **rest), shown


# The parse writes on the snapshot after the upload, the adapter sees it on the next one: `after`
# 2 is the undo's first read, 3 the read after its first clear, 5 the read after its second.
@pytest.mark.parametrize(
    ("step", "after"), [("captcha", 2), ("captcha", 3), ("captcha", 5), ("signin", 2)]
)
def test_a_step_for_the_person_after_the_parse_stops_the_undo_and_nothing_more_is_done(
    step: str, after: int
) -> None:
    form, shown = step_after_parse(step, after, files=("cv.pdf",))
    report = filled(form)
    assert shown, "the step never showed"
    assert [c for c in form.calls[shown[0] + 1 :] if c[0] not in PASSIVE] == []
    assert (report.stopped, report.needs_person) == ("needs_person", step)
    assert outcome(report, "Resume/CV").filled == ats_base.Filled("failed", step)


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


class LongParagraph(LeverForm):
    """Lever's form with an unnamed text box after `paragraphs`: its label is their words."""

    paragraphs: tuple[str, ...] = ()

    def render(self) -> str:
        if self._other is not None:
            return super().render()
        note = [f"  - generic [ref={self._ref('note')}]:"]
        note += [f"    - paragraph [ref={self._ref('note')}]: {p}" for p in self.paragraphs]
        note.append(f"    - textbox [ref={self._ref('note')}]")
        return "\n".join([super().render(), *note])


def long_paragraph(paragraphs: tuple[str, ...]) -> LongParagraph:
    model = lever_form()
    form = LongParagraph(model.fields, submit=model.submit, title=model.title)
    form.paragraphs = paragraphs
    return form


@pytest.mark.parametrize("sizes", [(9_000,), (500,) * 18], ids=["one", "eighteen"])
def test_a_label_read_from_long_paragraphs_is_cut_stored_and_approved(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    sizes: tuple[int, ...],
) -> None:
    # Random words, as natural text compresses under the index row's limit.
    rng = random.Random(331)  # noqa: S311, a repeatable draw of words, nothing secret
    texts = tuple("".join(rng.choices(string.ascii_letters + string.digits, k=n)) for n in sizes)
    on_lever(db, person_a, stub)
    app_id = started(db, person_a)
    assert (
        fill(services(factory, long_paragraph(texts), None, FakeAgent(), stub_base=stub), app_id)
        == "ready"
    )
    stored = answers_of(db, app_id)
    label = " ".join(texts)[: ats_base.NAME_MAX]
    assert label in stored
    assert stored[label] is None
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == COMMENTS)
        .values(value=LETTER)
    )
    db.commit()
    assert approve(factory, person_a, app_id).passed

    form = long_paragraph(texts)
    assert run_submit(submit_services(factory, form, stub), app_id) == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
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


@pytest.mark.parametrize("when", ["at_load", "after_the_parse"])
def test_a_refill_that_meets_hcaptcha_needs_the_person_and_nothing_is_typed_or_clicked(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, when: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    if when == "at_load":
        form, shown = lever_form(hcaptcha=True), [-1]
    else:
        form, shown = step_after_parse("captcha", 2)

    assert run_submit(submit_services(factory, form, stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert found.state is State.needs_person
    assert found.needs == "captcha"
    assert form.gate_clicks == []
    assert form.submit_clicks == 0
    assert shown, "the captcha never showed"
    assert [c for c in form.calls[shown[0] + 1 :] if c[0] not in PASSIVE] == []


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


def test_person_a_never_reads_lists_or_edits_bs_lever_application_or_answers(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    canary = "canary" + secrets.token_hex(8)
    # B's own answer to a question A's form leaves: it answers B's form, never A's.
    db.add(
        AnswerBankEntry(
            user_id=person_b.user_id,
            question_key=question_key(HEARD),
            question=HEARD,
            answer="Referral",
        )
    )
    db.commit()
    app_a = ready(db, factory, person_a, stub)
    app_b = ready(db, factory, person_b, stub)
    assert answers_of(db, app_b)[HEARD] == "Referral"
    assert answers_of(db, app_a)[HEARD] is None
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_b, ApplicationAnswer.label == "Phone")
        .values(value=f"+33 6 00 {canary}")
    )
    db.commit()
    _password(factory, person_a, person_b)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        base = f"/api/applications/{app_b}"
        view_b = api.get(f"{base}/approval", token=token_b).json()
        phone = next(a["field_key"] for a in view_b["answers"] if a["label"] == "Phone")
        shot = view_b["screenshots"][0]["id"]
        refused = [
            api.get(base, token=token_a),
            api.get(f"{base}/approval", token=token_a),
            api.get(f"{base}/screenshots/{shot}", token=token_a),
            api.request(
                "PUT",
                f"{base}/answers/{quote(phone, safe='')}",
                token=token_a,
                json={"value": "+33 6 11 11 11 11"},
            ),
        ]
        assert [r.status_code for r in refused] == [404] * len(refused)
        mine = api.get("/api/applications", token=token_a)
        theirs = api.get(f"/api/applications?posting_id={person_b.posting_id}", token=token_a)
        assert [item["id"] for item in mine.json()["items"]] == [str(app_a)]
        assert theirs.json()["items"] == []
        assert [r for r in (*refused, mine, theirs) if canary in r.text] == []
    assert answers_of(db, app_b)["Phone"] == f"+33 6 00 {canary}"


def served(
    settings: Settings, ctx: dict[str, Any], name: str, app_id: uuid.UUID, job_id: str
) -> None:
    """`name` queued for `app_id` on the apply queue as the tick queues it, then served by the
    worker's own apply queue (its crons off) until that queue is empty."""

    async def go() -> None:
        api_queue = queues.Queue(settings.redis_url.get_secret_value())
        try:
            assert await api_queue.enqueue(name, str(app_id), queue=queues.APPLY, job_id=job_id)
        finally:
            await api_queue.close()
        spec = dataclasses.replace(worker.apply_queue(settings), cron_jobs=())
        apply_worker = worker.build_worker(spec, settings, ctx)
        apply_worker.burst = True
        try:
            await apply_worker.main()
        finally:
            await worker.close_worker(apply_worker)

    asyncio.run(go())


def test_no_value_typed_on_lever_reaches_an_event_a_log_redis_or_a_job_result(
    db: Session,
    factory: sessionmaker[Session],
    migrated_url: str,
    redis_url: str,
    person_a: Person,
    stub: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    flush_redis(redis_url)
    caplog.set_level(logging.DEBUG)
    letter, phone = canaries.marker(), canaries.marker()
    version = db.get(DocumentVersion, person_a.letter_version_id)
    assert version is not None
    paragraphs = [*version.content["paragraphs"], f"Reference {letter}."]
    db.execute(
        sa.update(DocumentVersion)
        .where(DocumentVersion.id == version.id)
        .values(content={**version.content, "paragraphs": paragraphs})
    )
    db.commit()
    on_lever(db, person_a, stub)
    app_id = started(db, person_a)
    fill_job, submit_job = queues.apply_fill_job_id(app_id), queues.apply_submit_job_id(app_id)
    settings = make_settings(migrated_url, redis_url=redis_url, apply_stub_base_url=stub)
    client = redis_client.Redis.from_url(redis_url)
    # Redis as it stands each time a marker is typed: its keys, and where a marker is in it.
    typing: list[tuple[str, list[str], list[canaries.Finding]]] = []
    job = [fill_job]

    def look(form: Any, n: int) -> None:
        name, args = form.calls[-1]
        if name == "type" and (letter in args["text"] or phone in args["text"]):
            keys = [key.decode() for key in client.scan_iter(match="*")]
            typing.append((job[0], keys, canaries.find([letter, phone], redis=client)))

    leases = GateLeases()
    ctx: dict[str, Any] = {
        "settings": settings,
        "apply_services": services(
            factory, lever_form(on_call=look), None, FakeAgent(), stub_base=stub
        ),
        "apply_submit_services": submit_services(
            factory, lever_form(on_call=look), stub, leases=leases
        ),
    }
    try:
        served(settings, ctx, "apply_fill", app_id, fill_job)
        assert application(db, app_id).state is State.ready
        assert letter in answers_of(db, app_id)[COMMENTS]
        db.execute(
            sa.update(ApplicationAnswer)
            .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == "Phone")
            .values(value=f"+33 6 00 {phone}")
        )
        db.commit()
        assert approve(factory, person_a, app_id).passed
        job[0] = submit_job
        served(settings, ctx, "apply_submit", app_id, submit_job)
        assert application(db, app_id).state is State.submitted

        # Each marker was typed while its job's own record stood in the Redis read.
        assert {(at, job_key_prefix + at in keys) for at, keys, _ in typing} == {
            (fill_job, True),
            (submit_job, True),
        }
        assert [found for _, _, found in typing if found] == []
        events = db.execute(
            sa.select(ApplicationEvent.data).where(ApplicationEvent.application_id == app_id)
        ).scalars()
        found = canaries.find(
            [letter, phone],
            logs=caplog.records,
            redis=client,
            values=[*events, leases.acquired],
        )
        assert found == []
        left = [key.decode() for key in client.scan_iter(match="*")]
        assert [key for key in left if fill_job in key or submit_job in key] == []
    finally:
        client.close()


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
