"""Document-letter editing uses adapter provenance, never the editable label or request."""

import json
import unicodedata
import uuid
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.apply import gate, run
from app.apply.answer_limits import letter_issue
from app.apply.answers import Answer
from app.apply.approval_router import AnswerIn
from app.apply.ats import base
from app.apply.models import Application, ApplicationAnswer, State
from app.body_limit import limit_for
from tests.apply.a8_support import COMMENTS
from tests.apply.conftest import AT, Person
from tests.apply.test_a6_gate import _password, api_app, stub
from tests.apply.test_a8_lever import ready
from tests.identity.api_helpers import Api

__all__ = ["api_app", "stub"]


@pytest.mark.parametrize("codepoint", range(160))
def test_letter_typing_matches_the_browsers_control_character_contract(codepoint: int) -> None:
    character = chr(codepoint)
    refused = unicodedata.category(character) == "Cc" and character != "\n"
    assert letter_issue("before" + character + "after") == (
        "letter_control_character" if refused else None
    )


@pytest.mark.parametrize(
    "source", ["documents.letter_text", "documents.cv", "person", "agent.letter"]
)
def test_only_code_owned_letter_text_establishes_the_allowance(source: str) -> None:
    application = Application(id=uuid.uuid4(), user_id=uuid.uuid4())
    item = base.Field("text:cover letter#1", "Cover letter", "text")
    report = base.Report(
        [base.Outcome(item, Answer("letter", source), base.Filled("left", "letter_too_long"))]
    )
    (row,) = run._answer_rows(application, report)
    assert row.document_letter is (source == "documents.letter_text")
    assert row.left_reason == "letter_too_long"
    assert row.value is None
    assert gate.answer_max(row) == (10000 if source == "documents.letter_text" else 5000)


def test_the_schema_and_byte_cap_accept_the_largest_escaped_multibyte_letter() -> None:
    value = "\U0001f916" * 10000
    assert AnswerIn(value=value).value == value
    for path in (
        "/api/applications/one/answers/letter",
        "/api/applications/one/answers/text:cover/letter#1",
    ):
        assert limit_for("PUT", path) == 121024
        assert len(json.dumps({"value": value}, ensure_ascii=True).encode()) <= limit_for(
            "PUT", path
        )
    with pytest.raises(ValidationError):
        AnswerIn(value="x" * 10001)
    with pytest.raises(ValidationError):
        AnswerIn(value="x", document_letter=True)


def test_letter_edits_keep_their_allowance_and_ordinary_fields_cannot_forge_it(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    app_id = ready(db, factory, person_a, forms_base_url)
    _password(factory, person_a)
    rows = gate.rows_of(db, db.get(Application, app_id))
    letter = next(row for row in rows if row.label == COMMENTS)
    ordinary = next(row for row in rows if row.label == "Full name")
    assert letter.document_letter
    assert not ordinary.document_letter
    # A label resembling a letter does not change the persisted adapter classification.
    ordinary.label = "Cover letter"
    db.commit()
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        prefix = f"/api/applications/{app_id}/answers/"
        path = prefix + quote(letter.field_key, safe="")
        for length in (5000, 5001, 10000):
            value = "x" * length
            response = api.request("PUT", path, token=token, json={"value": value})
            assert response.status_code == 200, response.text
            current = next(
                row for row in response.json()["answers"] if row["field_key"] == letter.field_key
            )
            assert (current["value"], current["source"], current["max_chars"]) == (
                value,
                "person",
                10000,
            )
        assert api.request("PUT", path, token=token, json={"value": "x" * 10001}).status_code == 422
        for value in ("a\tb", "a\rb"):
            refused = api.request("PUT", path, token=token, json={"value": value})
            assert (refused.status_code, refused.json()["code"]) == (
                422,
                "letter_control_character",
            )
        multibyte = "\U0001f916" * 10000
        response = api.request(
            "PUT",
            path,
            token=token,
            content=json.dumps({"value": multibyte}, ensure_ascii=True),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text
        too_large = api.request(
            "PUT",
            path,
            token=token,
            content=" " * 121025,
            headers={"Content-Type": "application/json"},
        )
        assert too_large.status_code == 413
        ordinary_path = prefix + quote(ordinary.field_key, safe="")
        assert (
            api.request("PUT", ordinary_path, token=token, json={"value": "x" * 5000}).status_code
            == 200
        )
        assert (
            api.request("PUT", ordinary_path, token=token, json={"value": "x" * 5001}).status_code
            == 422
        )
        assert (
            api.request(
                "PUT",
                ordinary_path,
                token=token,
                json={"value": "x" * 5001, "document_letter": True},
            ).status_code
            == 422
        )
    db.expire_all()
    saved = db.scalar(
        sa.select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == app_id,
            ApplicationAnswer.field_key == letter.field_key,
        )
    )
    assert saved.document_letter
    assert saved.value == multibyte


@pytest.mark.parametrize("changed", [None, "label", "kind", "options"])
def test_the_letter_allowance_survives_only_the_same_field_on_refill(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    changed: str | None,
) -> None:
    app_id = ready(db, factory, person_a, forms_base_url)
    application = db.get(Application, app_id)
    old = next(row for row in gate.rows_of(db, application) if row.label == COMMENTS)
    gate.set_answer(db, person_a.user_id, app_id, old.field_key, "x" * 5001, AT)
    db.commit()
    application.state = State.filling
    db.commit()
    item = base.Field(
        old.field_key,
        "Another question" if changed == "label" else old.label,
        "combobox" if changed == "kind" else "text",
        options=("New option",) if changed == "options" else (),
    )
    report = base.Report([base.Outcome(item, run.Left("unknown"), base.Filled("left", "unknown"))])
    assert run.finish(db, app_id, run.Result(State.ready, report=report), AT, AT) == "ready"
    db.commit()
    (row,) = gate.rows_of(db, application)
    assert row.document_letter is (changed is None)
    assert row.value == ("x" * 5001 if changed is None else None)
    assert gate.answer_max(row) == (10000 if changed is None else 5000)
