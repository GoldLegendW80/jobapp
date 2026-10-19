"""D24: `POST /api/documents/base-cvs {from: upload}` through the app, two e2e people. A
conversion started (202, the base CV listed as uploaded, the default replacing one built from
the profile), waiting for the consent then for a credential, the file of another person and an
unknown id (404), a file whose text failed (422 with ingestion's own sentence), one still being
read (409), pages of pictures, a secret warning not confirmed, a text file (422 with their
sentences), the id missing (422), the 11th base CV (409), and the list of the other person,
which never shows it. Real Postgres at head."""

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
from app.agents.models import AgentRun, CredentialKind
from app.documents import base_router, consent, store
from app.documents.models import BaseSource, DocumentGeneration
from tests.agents.conftest import API_KEY, new_key
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-upload-alex@example.org"
BLAKE = "e2e-docs-upload-blake@example.org"
BASES = "/api/documents/base-cvs"
MORGAN = load("upload_morgan.txt")
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> Any:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, ALEX, clock)


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, BLAKE, clock)


def _upload(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    *,
    text: str | None = MORGAN,
    mime: str = PDF,
    status: str = "done",
    reason: str | None = None,
    secret: bool = False,
) -> str:
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text, "
                "text_source, text_status, failed_reason, failed_kind, secret_warning, "
                "parser_version) VALUES (:u, 'Morgan CV.pdf', :m, 10, :h, :t, 'parsed', "
                "CAST(:s AS ingestion_text_status), :r, CAST(:k AS ingestion_failed_kind), :w, "
                "2) RETURNING id"
            ),
            {
                "u": user,
                "m": mime,
                "h": hashlib.sha256(uuid.uuid4().bytes).digest(),
                "t": text,
                "s": status,
                "r": reason,
                "k": "file" if status == "failed" else None,
                "w": secret,
            },
        ).scalar_one()
        db.commit()
        return str(found)


def _credential(factory: sessionmaker[Session], user: uuid.UUID, clock: Any) -> None:
    with factory() as db:
        keyring = Keyring.from_text(new_key())
        credentials.save(db, keyring, user, CredentialKind.anthropic_api_key, API_KEY, clock())
        db.commit()


def _convert(api: Api, token: str, upload_id: str | None, **body: Any) -> Any:
    sent: dict[str, Any] = {"from": "upload", **body}
    if upload_id is not None:
        sent["ingestion_document_id"] = upload_id
    return api.request("POST", BASES, token=token, json=sent)


def _list(api: Api, token: str) -> list[dict[str, Any]]:
    response = api.request("GET", BASES, token=token)
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["base_cvs"]
    return found


def test_an_upload_is_converted_into_the_default_base_cv(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    with factory() as db:
        store.create_base(db, user, "From my profile", BaseSource.profile, at=clock())
        db.commit()
    response = _convert(api, token, _upload(factory, user))
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "private, no-store"
    made = response.json()["base_cv"]
    assert (made["source"], made["name"], made["template_slug"]) == (
        "uploaded",
        "Morgan CV",
        "classic",
    )
    assert made["is_default"]
    assert (made["generation"]["status"], made["generation"]["wait_reason"]) == (
        "waiting",
        "consent",
    )
    assert sorted((row["name"], row["is_default"]) for row in _list(api, token)) == [
        ("From my profile", False),
        ("Morgan CV", True),
    ]
    assert queue.jobs == []  # nothing is sent before the consent


def test_the_conversion_waits_for_a_credential_then_starts(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    accepted = api.request(
        "POST", "/api/documents/consent", token=token, json={"version": consent.VERSION}
    )
    assert accepted.status_code == 200
    response = _convert(api, token, _upload(factory, user, mime=DOCX), language="fr")
    generation = response.json()["base_cv"]["generation"]
    assert (generation["status"], generation["wait_reason"]) == ("waiting", "credential")
    assert response.json()["base_cv"]["language"] == "fr"
    _credential(factory, user, clock)
    second = _convert(api, token, _upload(factory, user, text=MORGAN + "\nAgain."))
    assert second.status_code == 202, second.text
    with factory() as db:
        modes = db.execute(
            sa.select(DocumentGeneration.mode, DocumentGeneration.status).where(
                DocumentGeneration.user_id == user
            )
        ).all()
        assert sorted((m.value, s.value) for m, s in modes) == [
            ("base_upload", "running"),
            ("base_upload", "running"),
        ]
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 2
    assert [job["function"] for job in queue.jobs] == ["run_agent", "run_agent"]


def test_another_persons_file_and_an_unknown_id_are_not_found(
    api: Api, alex: Any, blake: Any, factory: sessionmaker[Session]
) -> None:
    token, _ = alex
    _, other = blake
    for upload_id in (_upload(factory, other), str(uuid.uuid4())):
        response = _convert(api, token, upload_id)
        assert response.status_code == 404
        assert response.json()["detail"] == base_router.UPLOAD_NOT_FOUND
    assert _list(api, token) == []


@pytest.mark.parametrize(
    ("row", "status", "sentence"),
    [
        (
            {"status": "failed", "reason": "There is no text in this file.", "text": None},
            422,
            "There is no text in this file.",
        ),
        ({"status": "failed", "reason": None, "text": None}, 422, "failed"),
        ({"status": "pending", "text": None}, 409, "reading"),
        ({"status": "awaiting_transcription"}, 422, "pictures"),
        ({"secret": True}, 422, "secret"),
        ({"mime": "text/plain"}, 422, "not_a_cv"),
        ({"mime": "application/zip"}, 422, "not_a_cv"),
        ({"mime": "image/png"}, 422, "not_a_cv"),
    ],
)
def test_a_file_that_cannot_be_converted_is_refused_with_its_reason(
    api: Api,
    alex: Any,
    factory: sessionmaker[Session],
    row: dict[str, Any],
    status: int,
    sentence: str,
) -> None:
    token, user = alex
    response = _convert(api, token, _upload(factory, user, **row))
    assert response.status_code == status
    expected = base_router.UPLOAD_REFUSED.get(sentence, (status, sentence))[1]
    assert response.json()["detail"] == expected
    assert "—" not in expected
    assert " - " not in expected
    assert _list(api, token) == []


def test_a_confirmed_secret_warning_is_converted(
    api: Api, alex: Any, factory: sessionmaker[Session]
) -> None:
    token, user = alex
    upload_id = _upload(factory, user, secret=True)
    with factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET secret_confirmed_at = now() WHERE id = :i"),
            {"i": upload_id},
        )
        db.commit()
    assert _convert(api, token, upload_id).status_code == 202


def test_the_file_is_named(api: Api, alex: Any) -> None:
    token, _ = alex
    response = _convert(api, token, None)
    assert response.status_code == 422
    assert response.json()["detail"] == base_router.UPLOAD_MISSING


def test_the_eleventh_base_cv_is_refused(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    with factory() as db:
        for n in range(10):
            store.create_base(db, user, f"CV {n}", BaseSource.built, at=clock())
        db.commit()
    response = _convert(api, token, _upload(factory, user))
    assert response.status_code == 409
    assert response.json()["detail"] == base_router.too_many(10)
    assert len(_list(api, token)) == 10


def test_the_other_person_never_sees_the_converted_cv(
    api: Api, alex: Any, blake: Any, factory: sessionmaker[Session]
) -> None:
    token, user = alex
    other, _ = blake
    made = _convert(api, token, _upload(factory, user)).json()["base_cv"]
    assert _list(api, other) == []
    assert api.request("GET", f"/api/documents/{made['id']}", token=other).status_code == 404
    assert api.request("DELETE", f"{BASES}/{made['id']}", token=other).status_code == 404
