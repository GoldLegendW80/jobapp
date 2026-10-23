"""Saved answers start empty and are populated only by the person's explicit Settings action."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import run
from app.apply.ats import base
from app.apply.models import AnswerBankEntry, AnswerSource, ApplicationAnswer, State
from app.identity.deps import COOKIE_NAME
from tests.apply import a11_support as a11
from tests.apply.conftest import AT, Person
from tests.apply.test_a11_caps import api, stub
from tests.identity.api_helpers import Api

__all__ = ["api", "stub"]

PATH = "/api/apply/answer-bank"


def test_a_fresh_person_can_create_edit_and_forget_without_touching_another_bank(
    api: Api, person_a: Person, person_b: Person
) -> None:
    a, b = api.login_token(person_a.email), api.login_token(person_b.email)
    assert api.get(PATH, token=a).json() == {"items": []}
    created = api.post(PATH, token=a, json={"question": "  Prénom *  ", "answer": "  Ana  "})
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    entry = created.json()["items"][0]
    assert (entry["question_key"], entry["question"], entry["answer"], entry["used_count"]) == (
        "prenom",
        "Prénom *",
        "Ana",
        0,
    )
    assert api.get(PATH, token=b).json() == {"items": []}
    duplicate = api.post(PATH, token=a, json={"question": "PRENOM (optional)", "answer": "Other"})
    assert duplicate.status_code == 409
    assert api.get(PATH, token=a).json()["items"][0]["answer"] == "Ana"
    assert api.post(PATH, token=b, json={"question": "Prénom", "answer": "Bea"}).status_code == 201
    for answer in ("Anita", None):
        changed = api.request(
            "PUT", PATH, token=a, json={"question_key": "prenom", "answer": answer}
        )
        assert changed.status_code == 200
    assert api.get(PATH, token=a).json() == {"items": []}
    assert api.get(PATH, token=b).json()["items"][0]["answer"] == "Bea"
    assert (
        api.request(
            "PUT", PATH, token=a, json={"question_key": "prenom", "answer": "Ana"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "answer": "A"},
        {"question": "???", "answer": "A"},
        {"question": "東京", "answer": "A"},
        {"question": "Q" * 201, "answer": "A"},
        {"question": "Question", "answer": " "},
        {"question": "Question", "answer": "A" * 5001},
        {"question": "Question\nnext", "answer": "A"},
        {"question": "Question", "answer": "A\rB"},
        {"question": "Question", "answer": "A\tB"},
        {"question": "Question", "answer": "A\x00B"},
        {"question": "Question", "answer": "A\u202eB"},
        {"question": "Question", "answer": "A", "user_id": "foreign"},
        {"question": "Question", "answer": "A", "question_key": "trusted"},
    ],
)
def test_create_rejects_invalid_or_untrusted_inputs(api: Api, person_a: Person, body: dict) -> None:
    token = api.login_token(person_a.email)
    refused = api.post(PATH, token=token, json=body)
    assert refused.status_code == 422, refused.text
    assert api.get(PATH, token=token).json() == {"items": []}


def test_create_requires_session_and_csrf_and_preserves_unicode(api: Api, person_a: Person) -> None:
    body = {"question": "Favourite project?", "answer": "Équipe 🤖\n第二行"}
    assert api.post(PATH, json=body).status_code == 401
    token = api.login_token(person_a.email)
    assert api.post(PATH, token=token, csrf=False, json=body).status_code == 403
    assert api.post(PATH, token=token, json=body).json()["items"][0]["answer"] == body["answer"]


def test_two_creates_of_the_same_normalised_question_have_one_winner(
    api: Api, person_a: Person
) -> None:
    token = api.login_token(person_a.email)
    ready = Barrier(2)

    def create(question: str, answer: str):
        ready.wait(timeout=10)
        return api.client.post(
            PATH,
            headers={"Cookie": f"{COOKIE_NAME}={token}", "X-Requested-With": "jobapp"},
            json={"question": question, "answer": answer},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, "Prénom *", "Ana")
        second = pool.submit(create, "prenom (optional)", "Bea")
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(response.status_code for response in results) == [201, 409]
    winner = next(response for response in results if response.status_code == 201).json()["items"][
        0
    ]
    assert api.get(PATH, token=token).json()["items"] == [winner]


def test_maximum_saved_answer_survives_json_surrogate_escapes(api: Api, person_a: Person) -> None:
    import json

    token = api.login_token(person_a.email)
    body = {"question": "Q" + "🤖" * 199, "answer": "🤖" * 5000}
    response = api.post(
        PATH,
        token=token,
        content=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 201
    assert response.json()["items"][0]["answer"] == body["answer"]


@pytest.mark.parametrize(
    ("status", "expected"), [("filled", 1), ("unverified", 0), ("failed", 0), ("left", 0)]
)
def test_verified_bank_use_is_owner_bound_and_an_identical_refill_does_not_count_twice(
    db: Session, person_a: Person, person_b: Person, status: str, expected: int
) -> None:
    for person in (person_a, person_b):
        db.add(
            AnswerBankEntry(
                user_id=person.user_id,
                question_key="favourite robot",
                question="Favourite robot?",
                answer="R2",
            )
        )
    application = a11.application(db, person_a.user_id, person_a.posting_id, state=State.filling)
    db.commit()
    field = base.Field(key="robot", label="Favourite robot?", kind="text")
    report = base.Report(
        outcomes=[base.Outcome(field, run.Answer("R2", "bank"), base.Filled(status))]
    )
    result = run.Result(State.ready, report=report)
    for _ in range(2):
        application.state = State.filling
        db.commit()
        assert run.finish(db, application.id, result, AT, AT) == "ready"
        db.commit()
        db.expire_all()
        assert db.get(AnswerBankEntry, (person_a.user_id, "favourite robot")).used_count == expected
        assert db.get(AnswerBankEntry, (person_b.user_id, "favourite robot")).used_count == 0
    # An explicit application edit remains exact and does not become a bank use or bank write.
    saved = db.scalar(
        sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id)
    )
    saved.source, saved.value = AnswerSource.person, "My local answer"
    application.state = State.filling
    db.commit()
    run.finish(db, application.id, result, AT, AT)
    db.commit()
    db.expire_all()
    saved = db.scalar(
        sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id)
    )
    assert (saved.source, saved.value) == (AnswerSource.person, "My local answer")
    assert db.get(AnswerBankEntry, (person_a.user_id, "favourite robot")).answer == "R2"
    assert db.get(AnswerBankEntry, (person_a.user_id, "favourite robot")).used_count == expected
