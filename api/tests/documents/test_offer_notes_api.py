"""The notes on an offer letter through the api, two e2e people: read before any notes, a click that
starts the reading (202, its run queued), the refusals (an upload not read yet, another person's
upload or posting, no credential, a reading open; no consent asked), the notes left out of the
posting's CV and letter, their chat through the documents chat, and B's notes 404 to A. Real
Postgres."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind
from app.documents import chat
from app.documents.models import Document, Kind
from tests.agents.conftest import API_KEY, new_key
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-offer-alex@example.org"
BLAKE = "e2e-docs-offer-blake@example.org"
LETTER = "Lumen Example offers you the role of platform engineer. Pay: 4,500 EUR a month."


def path(posting: uuid.UUID) -> str:
    return f"/api/documents/offer-notes/{posting}"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> dict[str, Any]:
    """A person with a profile, a credential and no consent, a posting at interview and an
    uploaded letter whose text ingestion read."""
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    with factory() as db:
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, status) VALUES "
                "(:u, :s, 'Lumen Example', 'Platform engineer', 'interview') RETURNING id"
            ),
            {"u": user.id, "s": f"lumen-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        letter = db.execute(
            sa.text(
                "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text, "
                "text_source, text_status, parser_version) VALUES (:u, 'offer.pdf', "
                "'application/pdf', 80, :h, :t, 'parsed', 'done', 2) RETURNING id"
            ),
            {"u": user.id, "h": hashlib.sha256(email.encode()).digest(), "t": LETTER},
        ).scalar_one()
        credentials.save(
            db,
            Keyring.from_text(new_key()),
            user.id,
            CredentialKind.anthropic_api_key,
            API_KEY,
            clock(),
        )
        db.commit()
    return {"token": api.login_token(email), "user": user.id, "posting": posting, "letter": letter}


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, ALEX, clock)


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, BLAKE, clock)


def _read(api: Api, who: dict[str, Any], letter: Any = None) -> Any:
    return api.request(
        "POST",
        path(who["posting"]),
        token=who["token"],
        json={"ingestion_document_id": str(letter or who["letter"])},
    )


def test_before_any_reading_the_posting_has_no_notes(api: Api, alex: dict[str, Any]) -> None:
    answer = api.get(path(alex["posting"]), token=alex["token"])
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "private, no-store"
    body = answer.json()
    assert (body["document_id"], body["reading"], body["busy"]) == (None, None, False)
    assert body["pay"] == {
        "posting": {
            "title": "Platform engineer",
            "company": "Lumen Example",
            "contract": "CDI",
            "location": None,
            "work_mode": None,
            "pay": None,
        },
        "pay_sought": [],
    }


def test_a_click_reads_the_letter_and_queues_its_run(
    api: Api, alex: dict[str, Any], queue: RecordingQueue, factory: sessionmaker[Session]
) -> None:
    answer = _read(api, alex)
    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert body["reading"]["status"] == "running"
    assert body["letter"] == {"id": str(alex["letter"]), "name": "offer.pdf"}
    assert (body["busy"], body["language"]) == (True, "en")
    assert [job["function"] for job in queue.jobs] == ["run_agent"]
    again = _read(api, alex)
    assert again.status_code == 409
    assert again.json()["detail"] == "The notes are being written. Try again once they are done."
    listed = api.get(f"/api/jobs/{alex['posting']}/documents", token=alex["token"]).json()
    assert listed["documents"] == []
    with factory() as db:
        kinds = db.scalars(sa.select(Document.kind).where(Document.posting_id == alex["posting"]))
        assert list(kinds) == [Kind.offer_notes]


def test_a_letter_not_read_yet_or_without_a_credential_starts_nothing(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session], queue: RecordingQueue
) -> None:
    with factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET text_status = 'pending' WHERE id = :d"),
            {"d": alex["letter"]},
        )
        db.commit()
    answer = _read(api, alex)
    assert answer.status_code == 422
    assert answer.json()["detail"] == "The letter is still being read. Try again in a moment."
    with factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET text_status = 'done' WHERE id = :d"),
            {"d": alex["letter"]},
        )
        credentials.delete(db, alex["user"])
        db.commit()
    answer = _read(api, alex)
    assert answer.status_code == 409
    assert answer.json()["detail"] == (
        "Add a model credential on the Account page first, then read the letter again."
    )
    assert queue.jobs == []
    assert api.get(path(alex["posting"]), token=alex["token"]).json()["document_id"] is None


def test_one_persons_letter_posting_and_notes_are_not_the_others(
    api: Api, alex: dict[str, Any], blake: dict[str, Any]
) -> None:
    assert _read(api, alex, blake["letter"]).status_code == 404
    assert api.get(path(blake["posting"]), token=alex["token"]).status_code == 404
    assert _read(api, {**alex, "posting": blake["posting"]}).status_code == 404
    document = _read(api, blake).json()["document_id"]
    assert api.get(f"/api/documents/{document}/messages", token=alex["token"]).status_code == 404
    sent = api.request(
        "POST",
        f"/api/documents/{document}/messages",
        token=alex["token"],
        json={"text": "Add a question."},
    )
    assert sent.status_code == 404


def test_the_chat_of_the_notes_waits_for_the_reading(api: Api, alex: dict[str, Any]) -> None:
    document = _read(api, alex).json()["document_id"]
    sent = api.request(
        "POST",
        f"/api/documents/{document}/messages",
        token=alex["token"],
        json={"text": "Add a question about the trial period."},
    )
    assert sent.status_code == 409
    assert (
        sent.json()["detail"]
        == "The assistant is still answering the last message of this document."
    )


def _reading_done(factory: sessionmaker[Session], who: dict[str, Any]) -> None:
    with factory() as db:
        db.execute(
            sa.text("UPDATE agent_runs SET status = 'done' WHERE user_id = :u"), {"u": who["user"]}
        )
        db.execute(
            sa.text("UPDATE document_generations SET status = 'done' WHERE user_id = :u"),
            {"u": who["user"]},
        )
        db.commit()


def _consents(factory: sessionmaker[Session], who: dict[str, Any]) -> int:
    with factory() as db:
        return db.scalar(
            sa.text("SELECT count(*) FROM ingestion_consents WHERE user_id = :u"),
            {"u": who["user"]},
        )


def test_without_any_consent_the_reading_starts_and_its_chat_answers(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session], queue: RecordingQueue
) -> None:
    assert _consents(factory, alex) == 0
    read = _read(api, alex)
    assert read.status_code == 202, read.text
    _reading_done(factory, alex)
    sent = api.request(
        "POST",
        f"/api/documents/{read.json()['document_id']}/messages",
        token=alex["token"],
        json={"text": "Add a question about the trial period."},
    )
    assert sent.status_code == 202, sent.text
    assert (sent.json()["generation"]["status"], sent.json()["message"]["role"]) == (
        "running",
        "user",
    )
    assert [job["function"] for job in queue.jobs] == ["run_agent", "run_agent"]
    assert _consents(factory, alex) == 0


def test_the_chat_of_the_notes_without_a_credential_answers_409(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session]
) -> None:
    document = _read(api, alex).json()["document_id"]
    _reading_done(factory, alex)
    with factory() as db:
        credentials.delete(db, alex["user"])
        db.commit()
    sent = api.request(
        "POST",
        f"/api/documents/{document}/messages",
        token=alex["token"],
        json={"text": "Add a question about the trial period."},
    )
    assert sent.status_code == 409
    assert sent.json()["detail"] == chat.NO_CREDENTIAL
