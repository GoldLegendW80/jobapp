"""Remember this answer for next forms (owner, 2026-11-05, answer 6; `remember.py` and the
`/remember` route of `approval_router.py`), on a real Postgres at head. The tables say which
questions name the company and which are sensitive; the routes save the person's answer to
Saved answers, and the next form with the same question is filled with it. Every account is
`e2e-*@example.org`; the companies and the answers are invented.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import answers, bank, remember
from app.apply.models import (
    AnswerBankEntry,
    AnswerSource,
    ApplicationAnswer,
    ApplicationEvent,
)
from app.main import create_app
from app.profile import service as profile_service
from tests.apply.a6_support import FakeQueue
from tests.apply.conftest import AT, Person
from tests.apply.test_a6_gate import _password
from tests.apply.test_a12_likes import SECRET_KEY, filled, form, like, posting
from tests.conftest import make_settings
from tests.identity.api_helpers import Api
from tests.profile.conftest import full

COMPANY = "Example Robotics SAS"

# --- names the company ---------------------------------------------------------------------------

NAMES_COMPANY = [
    "Have you worked at Example Robotics before?",
    "Have you ever worked for EXAMPLE ROBOTICS?",
    "Have you worked at Example before?",
    "Do you know anyone at ExampleRobotics?",
    "Have you applied to Example Robotics SAS in the last year?",
    "Are you a current employee of Example Robotics, Inc.?",
    "Have you worked for us before?",
    "How did you hear about us?",
    "Why do you want to join our team?",
    "Have you previously been employed by this company?",
    "Are you related to anyone at this organisation?",
    "Do you know someone who works at the company?",
    "Avez-vous déjà travaillé chez nous ?",
    "Pourquoi notre entreprise ?",
    "Connaissez-vous quelqu'un dans cette entreprise ?",
    "Example: what is your notice period?",
]
NAMES_NO_COMPANY = [
    "What is your notice period?",
    "Notice period",
    "Are you willing to relocate?",
    "Are you legally authorised to work in the European Union?",
    "Will you now or in the future require visa sponsorship?",
    "Do you have robotics experience?",
    "How many years of Python do you have?",
    "Which time zone do you work in?",
    "Are you open to travel up to 20 percent?",
    "Do you hold a driving licence?",
    "Earliest start date",
    "Quel est votre préavis ?",
    "Êtes-vous mobile géographiquement ?",
    "Do you have experience with warehouse systems?",
    "Have you worked with SAS software?",
    "What is your preferred working language?",
    "Are you comfortable working on site in Paris?",
    "Do you trust your team?",
    "Can you start within one month?",
    "Highest degree obtained",
]


@pytest.mark.parametrize("label", NAMES_COMPANY)
def test_a_question_that_names_the_company_is_unticked(label: str) -> None:
    assert remember.names_company(label, COMPANY) is True


@pytest.mark.parametrize("label", NAMES_NO_COMPANY)
def test_a_question_that_names_no_company_is_ticked(label: str) -> None:
    assert remember.names_company(label, COMPANY) is False


@pytest.mark.parametrize("company", [None, "", "SAS", "  ", "Inc."])
def test_an_unknown_company_leaves_the_box_unticked(company: str | None) -> None:
    assert remember.names_company("What is your notice period?", company) is True


def test_a_short_name_counts_whole_and_never_inside_a_word() -> None:
    assert remember.names_company("Have you worked at HP?", "HP Inc") is True
    assert remember.names_company("Do you know PHP?", "HP Inc") is False


# --- sensitive questions -------------------------------------------------------------------------

SENSITIVE = [
    "Do you have any health condition we should know about?",
    "Do you need any accommodation during the interview?",
    "Do you require any reasonable accommodations?",
    "Are you vaccinated against COVID-19?",
    "Are you willing to take a drug test?",
    "Do you have any medical restrictions?",
    "Are you pregnant or planning a maternity leave?",
    "What is your religion?",
    "Are you a member of a trade union?",
    "Political affiliation",
    "Marital status",
    "Are you married?",
    "How many children do you have?",
    "Do you have dependents?",
    "Avez-vous un problème de santé ?",
    "Êtes-vous enceinte ?",
    "Situation familiale",
    "Do you have any allergies?",
    "Do you suffer from any illness or disease?",
    "Mental health support needs",
    "Blood type",
    "Do you consent to biometric checks?",
]


@pytest.mark.parametrize("label", SENSITIVE)
def test_a_sensitive_question_is_never_offered(label: str) -> None:
    field = answers.FormField(label, "text", (), True)
    context = answers.Context(uuid.uuid4(), {})
    assert remember.offered(field, context, COMPANY) is None
    assert remember.check(field, context, "No") is None


# What the resolver itself keeps away from Saved answers: never offered either.
POLICY = [
    ("What is your gender?", "text"),
    ("Do you identify as having a disability?", "yes_no"),
    ("Are you a veteran?", "yes_no"),
    ("What is your ethnicity?", "text"),
    ("What are your salary expectations?", "text"),
    ("What is your current salary?", "text"),
    ("Name of your referrer", "text"),
    ("How old are you?", "text"),
    ("What is your nationality?", "text"),
    ("Do you have a criminal record?", "yes_no"),
    ("Why do you want this role?", "textarea"),
    ("Resume", "file"),
    ("I agree to the processing of my data for this application.", "checkbox"),
    ("Which tools do you use?", "multi"),
]


@pytest.mark.parametrize(("label", "kind"), POLICY)
def test_what_the_resolver_never_reads_from_the_bank_is_never_offered(
    label: str, kind: answers.FieldKind
) -> None:
    field = answers.FormField(label, kind, (), True)
    context = answers.Context(uuid.uuid4(), {"answers": full("answers")})
    assert remember.offered(field, context, COMPANY) is None


# Questions the profile cannot answer, which a saved answer then fills.
OFFERED = [
    ("Do you hold a driving licence?", "yes_no", (), True),
    ("Which time zone do you work in?", "text", (), "Central European Time"),
    ("Preferred interview language", "select", ("English", "French"), "French"),
    ("Do you have a security clearance level?", "text", (), "None"),
    ("Can you start within one month?", "yes_no", ("Yes", "No"), False),
]


@pytest.mark.parametrize(("label", "kind", "options", "value"), OFFERED)
def test_an_answer_the_bank_gives_back_is_offered_ticked_and_checked_exactly(
    label: str, kind: answers.FieldKind, options: tuple[str, ...], value: str | bool
) -> None:
    field = answers.FormField(label, kind, options, True)
    context = answers.Context(uuid.uuid4(), {})
    assert remember.offered(field, context, COMPANY) == "on"
    assert remember.check(field, context, value) == (
        ("Yes" if value else "No") if isinstance(value, bool) else value
    )


def test_an_answer_that_does_not_fit_the_question_is_not_remembered() -> None:
    choice = answers.FormField("Preferred interview language", "select", ("English", "French"))
    context = answers.Context(uuid.uuid4(), {})
    assert remember.check(choice, context, "German") is None
    date = answers.FormField("Available from", "date")
    assert remember.check(date, context, "next month") is None
    assert remember.check(date, context, "2026-12-01") == "2026-12-01"
    text = answers.FormField("Which time zone do you work in?", "text")
    assert remember.check(text, context, "x\x00y") is None
    assert remember.check(text, context, "   ") is None
    assert remember.check(text, context, "a" * (bank.VALUE_MAX + 1)) is None
    assert remember.check(text, context, ["a"]) is None


def test_a_question_the_profile_answers_is_not_offered() -> None:
    """The profile comes first: a saved answer to its question would never be read."""
    field = answers.FormField("Email", "text", (), True)
    context = answers.Context(uuid.uuid4(), {"identity": full("identity")})
    assert remember.offered(field, context, COMPANY) is None


# --- the routes ----------------------------------------------------------------------------------

ASKED = "Will you now or in the future require visa sponsorship?"


@pytest.fixture
def api_app(migrated_url: str, forms_base_url: str) -> FastAPI:
    settings = make_settings(
        migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=forms_base_url
    )
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def _no_work_authorisation(db: Session, user_id: uuid.UUID) -> None:
    profile = profile_service.get_profile(db, user_id, AT)
    content = {**full("answers"), "work_authorization": []}
    profile_service.save_section(db, user_id, "answers", content, profile.revision, AT)
    db.commit()


def test_a_remembered_answer_fills_the_next_form_by_itself(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    stub = forms_base_url
    _password(factory, person_a, person_b)
    _no_work_authorisation(db, person_a.user_id)
    first = like(db, person_a, posting(db, person_a.user_id, form(stub))).application.id
    # A required question the profile cannot answer: the form waits for the person.
    assert filled(factory, first, stub) == "needs_person"
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        view = api.get(f"/api/applications/{first}/approval", token=token).json()
        by_label = {one["label"]: one for one in view["answers"]}
        asked = by_label[ASKED]
        assert (asked["you_answer"], asked["remember"]) == (True, "on")
        why = by_label["Why Example Robotics?"]
        assert (why["you_answer"], why["remember"]) == (True, None)
        # The free text and the legal box are never offered; a filled field carries nothing.
        assert all(one["remember"] is None for one in view["answers"] if not one["you_answer"])
        path = f"/api/applications/{first}/remember"
        key = asked["field_key"]

        # Nothing is remembered before the person's answer is saved on this form.
        early = api.post(path, token=token, json={"field_key": key, "value": False})
        assert (early.status_code, early.json()["code"]) == (409, "not_answered")
        saved = api.request(
            "PUT",
            f"/api/applications/{first}/answers/{quote(key, safe='')}",
            token=token,
            json={"value": False},
        )
        assert saved.status_code == 200
        # Editing the application saved nothing to Saved answers (owner, 2026-10-23).
        assert api.get("/api/apply/answer-bank", token=token).json()["items"] == []

        # Another value than the one saved, another person, no CSRF: nothing is saved.
        other = api.post(path, token=token, json={"field_key": key, "value": True})
        assert other.json()["code"] == "not_answered"
        assert (
            api.post(path, token=token_b, json={"field_key": key, "value": False}).status_code
            == 404
        )
        assert (
            api.post(
                path, token=token, json={"field_key": key, "value": False}, csrf=False
            ).status_code
            == 403
        )
        assert api.get("/api/apply/answer-bank", token=token).json()["items"] == []

        done = api.post(path, token=token, json={"field_key": key, "value": False})
        assert done.status_code == 200
        assert done.json() == {"outcome": "saved", "question": ASKED}
        [entry] = api.get("/api/apply/answer-bank", token=token).json()["items"]
        assert (entry["question"], entry["answer"], entry["used_count"]) == (ASKED, "No", 0)
        again = api.post(path, token=token, json={"field_key": key, "value": False})
        assert again.json()["outcome"] == "kept"
        assert api.get("/api/apply/answer-bank", token=token_b).json()["items"] == []
        hash_after = api.get(f"/api/applications/{first}/approval", token=token).json()
        assert hash_after["answers_hash"] == saved.json()["answers_hash"]

    events = db.execute(
        sa.select(ApplicationEvent.data).where(
            ApplicationEvent.application_id == first,
            ApplicationEvent.kind == "answer_remembered",
        )
    ).scalars()
    assert [e["outcome"] for e in events] == ["saved", "kept"]

    # The next form with the same question: filled from Saved answers, so it waits for nobody.
    second = like(
        db, person_a, posting(db, person_a.user_id, form(stub), title="ML Engineer")
    ).application.id
    filled(factory, second, stub)
    db.expire_all()
    rows = db.scalars(
        sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == second)
    ).all()
    row = next(r for r in rows if r.label == ASKED)
    # The form offers Yes and No: the saved No picks its option.
    assert (row.source, row.value) == (AnswerSource.bank, "No")
    # Only the free text the agent writes is still the person's to answer.
    left = [r.label for r in rows if r.source is AnswerSource.left and r.required]
    assert left == ["Why Example Robotics?"]


def test_a_sensitive_or_policy_answer_is_refused_by_the_route(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    """The route checks again, whatever the page offered: the free text answer to "Why Example
    Robotics?" is never stored, nor an answer to a field the form does not have."""
    stub = forms_base_url
    _password(factory, person_a)
    first = like(db, person_a, posting(db, person_a.user_id, form(stub))).application.id
    assert filled(factory, first, stub) == "ready"
    with factory() as s:
        why = s.scalar(
            sa.select(ApplicationAnswer).where(
                ApplicationAnswer.application_id == first,
                ApplicationAnswer.label == "Why Example Robotics?",
            )
        )
        assert why is not None
        why.source, why.value = AnswerSource.person, "I build agents."
        s.commit()
        key = why.field_key
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        path = f"/api/applications/{first}/remember"
        refused = api.post(path, token=token, json={"field_key": key, "value": "I build agents."})
        assert (refused.status_code, refused.json()["code"]) == (422, "not_rememberable")
        unknown = api.post(path, token=token, json={"field_key": "nope", "value": "x"})
        assert unknown.json()["code"] == "not_rememberable"
        assert api.post(path, token=token, json={"field_key": key}).status_code == 422
    assert db.scalar(sa.select(sa.func.count()).select_from(AnswerBankEntry)) == 0
