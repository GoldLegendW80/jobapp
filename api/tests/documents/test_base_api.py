"""D9: the base CVs and the consent through the app, two e2e people. The list and its default
(made twice, deleted with others and last), a build from the profile (waiting for the consent,
then for a credential, then started), a build from an empty profile (a skeleton with its note),
a copy (and a copy of another person's, 404), the 11th refused with its sentence, a delete that
leaves the posting CVs written from it, the upload answered 501, the consent (its pinned text,
its version, accepted twice, the text bumped), its estimates, and a claim added by hand to a
base CV that stays a note at every save."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.documents import base_router, consent, store, writer_base
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationStatus,
    Kind,
)
from tests.agents.conftest import API_KEY, new_key
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-base-alex@example.org"
BLAKE = "e2e-docs-base-blake@example.org"
BASES = "/api/documents/base-cvs"
CONSENT = "/api/documents/consent"
# SHA-256 of each consent text: editing the words of a version fails here (consent.py).
DIGESTS = {1: "34d57d47024662f34306e675e84339a437d499faa24e64a51ed6f1db9a2d1f51"}


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(
    factory: sessionmaker[Session], api: Api, email: str, clock: Any, *, profile: bool = True
) -> tuple[str, uuid.UUID]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    if profile:
        give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def _credential(factory: sessionmaker[Session], user: uuid.UUID, clock: Any) -> None:
    with factory() as db:
        keyring = Keyring.from_text(new_key())
        credentials.save(db, keyring, user, CredentialKind.anthropic_api_key, API_KEY, clock())
        db.commit()


def _consent(api: Api, token: str) -> Any:
    return api.request("POST", CONSENT, token=token, json={"version": consent.VERSION})


def _build(api: Api, token: str, **body: Any) -> Any:
    return api.request("POST", BASES, token=token, json={"from": "profile", **body})


def _list(api: Api, token: str) -> list[dict[str, Any]]:
    response = api.request("GET", BASES, token=token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    found: list[dict[str, Any]] = response.json()["base_cvs"]
    return found


def _bases(factory: sessionmaker[Session], user: uuid.UUID, clock: Any, count: int) -> list[str]:
    with factory() as db:
        made = [
            store.create_base(
                db, user, f"CV {n}", BaseSource.built, at=clock() + timedelta(seconds=n)
            ).id
            for n in range(count)
        ]
        db.commit()
    return [str(found) for found in made]


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, ALEX, clock)


# --- the consent --------------------------------------------------------------------------------


def test_consent_texts_are_pinned() -> None:
    digests = {
        version: hashlib.sha256(words.encode()).hexdigest()
        for version, words in consent.TEXTS.items()
    }
    assert digests == DIGESTS
    assert max(consent.TEXTS) == consent.VERSION
    for words in consent.TEXTS.values():
        assert "\u2014" not in words
        assert "\u2013" not in words
        assert " - " not in words


def test_consent_is_asked_recorded_once_and_asked_again_when_the_text_changes(
    api: Api, alex: Any, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    token, user = alex
    first = api.request("GET", CONSENT, token=token).json()
    assert first["version"] == 1
    assert first["accepted"] is False
    assert first["text"] == consent.TEXTS[1]
    assert "pictures" in first["text"]
    assert first["model"] is None  # no credential yet
    assert first["posting"]["tokens"]["low"] < first["posting"]["tokens"]["high"]
    assert first["posting"]["cost"] is None
    assert first["week"]["postings"] == 0

    stale = api.request("POST", CONSENT, token=token, json={"version": 2})
    assert stale.status_code == 422
    assert stale.json()["detail"] == consent.OUTDATED
    accepted = _consent(api, token)
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True
    again = _consent(api, token).json()
    assert again["accepted_at"] == accepted.json()["accepted_at"]  # the first date stays

    monkeypatch.setattr(consent, "TEXTS", {**consent.TEXTS, 2: "A new text."})
    monkeypatch.setattr(consent, "VERSION", 2)
    bumped = api.request("GET", CONSENT, token=token).json()
    assert bumped["version"] == 2
    assert bumped["accepted"] is False
    with factory() as db:
        assert writer_base.wait_reason(db, user) is not None


def test_consent_estimates_count_the_last_30_days_at_or_above_the_threshold(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    _credential(factory, user, clock)
    today = clock().date()
    rows = [(90, 0), (70, 29), (69, 1), (None, 2), (95, 30)]  # (fit, days ago)
    with factory() as db:
        for fit, ago in rows:
            db.execute(
                sa.text(
                    "INSERT INTO jobs (user_id, slug, company, title, fit, found_on) VALUES "
                    "(:u, :s, 'Example SA', 'Engineer', :f, :d)"
                ),
                {"u": user, "s": uuid.uuid4().hex, "f": fit, "d": today - timedelta(days=ago)},
            )
        db.commit()
    body = api.request("GET", CONSENT, token=token).json()
    assert body["min_fit"] == 70
    assert body["week"]["postings_30_days"] == 2  # 90 today and 70 at 29 days; not 69, None, 30
    assert body["week"]["postings"] == 1  # 2 * 7 / 30, rounded up
    assert body["model"] == "claude-sonnet-5-5"
    assert body["posting"]["cost"]["currency"] == "USD"
    assert body["week"]["tokens"] == body["posting"]["tokens"]


# --- building from the profile ------------------------------------------------------------------


def test_a_build_waits_for_the_consent_then_the_credential_then_starts(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    response = _build(api, token)
    assert response.status_code == 202, response.text
    built = response.json()["base_cv"]
    assert built["source"] == "built"
    assert built["template_slug"] == "classic"
    assert built["is_default"] is True
    assert built["name"] == "My CV"
    assert built["generation"]["status"] == "waiting"
    assert built["generation"]["wait_reason"] == "consent"

    assert _consent(api, token).status_code == 200
    (row,) = _list(api, token)
    assert row["generation"]["wait_reason"] == "credential"  # nothing sent, nothing counted
    assert not queue.jobs

    _credential(factory, user, clock)
    with factory() as db:
        runs = writer_base.start_ready(db, user, at=clock())
        db.commit()
    assert len(runs) == 1
    (row,) = _list(api, token)
    assert row["generation"]["status"] == "running"
    with factory() as db:
        run = db.get(AgentRun, runs[0])
        assert run is not None
        assert run.agent == writer_base.NAME
        assert run.job_id is None
        generation = db.get(DocumentGeneration, uuid.UUID(row["generation"]["id"]))
        assert generation is not None
        assert generation.token_ceiling > 0
        assert generation.token_ceiling == generation.estimate["ceiling"]


def test_a_build_with_consent_and_credential_starts_its_run_at_once(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    _credential(factory, user, clock)
    _consent(api, token)
    response = _build(api, token, name="  For platform roles  ")
    assert response.status_code == 202
    built = response.json()["base_cv"]
    assert built["name"] == "For platform roles"
    assert built["generation"]["status"] == "running"
    assert [job["function"] for job in queue.jobs] == ["run_agent"]
    with factory() as db:
        document = db.get(Document, uuid.UUID(built["id"]))
        assert document is not None
        employers = [e["key"] for e in document.draft["fixed"]["employers"]]
        assert employers == ["example-corp", "sample-labs"]  # copied from the profile by code


def test_an_empty_profile_gives_a_skeleton_with_a_note_and_no_generation(
    api: Api, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = _person(factory, api, ALEX, clock, profile=False)
    _credential(factory, user, clock)
    _consent(api, token)
    response = _build(api, token)
    assert response.status_code == 202  # nothing refused
    built = response.json()["base_cv"]
    assert built["generation"] is None
    with factory() as db:
        document = db.get(Document, uuid.UUID(built["id"]))
        assert document is not None
        assert document.draft["notes"] == writer_base.FILL_PROFILE
    assert [job["function"] for job in queue.jobs] == ["render_document"]


# --- copies, the limit, the default, deletes ---------------------------------------------------


def test_a_copy_takes_the_draft_and_template_and_another_persons_is_404(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    blake, blake_id = _person(factory, api, BLAKE, clock)
    with factory() as db:
        source = store.create_base(
            db,
            user,
            "Original",
            BaseSource.built,
            at=clock(),
            content=load("base_cv_alex.json"),
            template_slug="sidebar",
        )
        db.commit()
        source_id, draft = str(source.id), source.draft
    response = api.request("POST", BASES, token=token, json={"from": "copy", "copy_of": source_id})
    assert response.status_code == 201, response.text
    copy = response.json()["base_cv"]
    assert copy["name"] == "Copy of Original"
    assert copy["template_slug"] == "sidebar"
    assert copy["source"] == "built"
    assert copy["is_default"] is False
    assert copy["generation"] is None
    with factory() as db:
        made = db.get(Document, uuid.UUID(copy["id"]))
        assert made is not None
        assert made.draft == draft

    stolen = api.request("POST", BASES, token=blake, json={"from": "copy", "copy_of": source_id})
    assert stolen.status_code == 404
    missing = api.request("POST", BASES, token=token, json={"from": "copy"})
    assert missing.status_code == 422
    assert _list(api, blake) == []
    for method, body in (("PATCH", {"name": "Mine"}), ("DELETE", None)):
        assert (
            api.request(method, f"{BASES}/{source_id}", token=blake, json=body).status_code == 404
        )
    assert len(_list(api, token)) == 2
    assert blake_id != user


def test_the_eleventh_base_cv_is_refused_with_the_sentence(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    made = _bases(factory, user, clock, 10)
    for body in ({"from": "profile"}, {"from": "copy", "copy_of": made[0]}):
        response = api.request("POST", BASES, token=token, json=body)
        assert response.status_code == 409
        assert response.json()["detail"] == base_router.too_many(10)
    assert len(_list(api, token)) == 10


def test_the_default_moves_twice_the_same_and_the_oldest_takes_over(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    first, second, third = _bases(factory, user, clock, 3)
    assert [row["is_default"] for row in _list(api, token)] == [True, False, False]
    for _ in range(2):
        response = api.request("PATCH", f"{BASES}/{third}", token=token, json={"default": True})
        assert response.status_code == 200
        assert response.json()["is_default"] is True
    assert [row["is_default"] for row in _list(api, token)] == [False, False, True]
    refused = api.request("PATCH", f"{BASES}/{third}", token=token, json={"default": False})
    assert refused.status_code == 422
    renamed = api.request("PATCH", f"{BASES}/{first}", token=token, json={"name": "Data roles"})
    assert renamed.json()["name"] == "Data roles"
    empty = api.request("PATCH", f"{BASES}/{first}", token=token, json={"name": "   "})
    assert empty.status_code == 422

    assert api.request("DELETE", f"{BASES}/{third}", token=token).status_code == 204
    rows = _list(api, token)
    assert [(row["id"], row["is_default"]) for row in rows] == [(first, True), (second, False)]
    for found in (first, second):
        assert api.request("DELETE", f"{BASES}/{found}", token=token).status_code == 204
    assert _list(api, token) == []
    assert api.request("DELETE", f"{BASES}/{first}", token=token).status_code == 404


def test_deleting_a_base_cv_leaves_the_posting_cvs_written_from_it(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    (base,) = _bases(factory, user, clock, 1)
    with factory() as db:
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) VALUES "
                "(:u, 'lumen-1', 'Lumen', 'Engineer') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        document = store.get_or_create(db, user, posting, Kind.cv, at=clock())
        document.base_document_id = uuid.UUID(base)
        db.commit()
        document_id = document.id
    assert api.request("DELETE", f"{BASES}/{base}", token=token).status_code == 204
    with factory() as db:
        kept = db.get(Document, document_id)
        assert kept is not None
        assert kept.base_document_id is None


def test_from_an_upload_answers_501_until_d24(api: Api, alex: Any) -> None:
    token, _ = alex
    response = api.request(
        "POST",
        BASES,
        token=token,
        json={"from": "upload", "ingestion_document_id": str(uuid.uuid4())},
    )
    assert response.status_code == 501
    assert response.json()["detail"] == base_router.UPLOAD_LATER


# --- a claim added by hand ----------------------------------------------------------------------


def test_a_claim_added_by_hand_to_a_base_cv_is_a_note_at_every_save(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any
) -> None:
    token, user = alex
    with factory() as db:
        view = writer_base.view_of(db, user, clock())
        content = writer_base.skeleton(view).model_dump(mode="json", by_alias=True)
        base = store.create_base(db, user, "Mine", BaseSource.built, at=clock(), content=content)
        db.commit()
        base_id = str(base.id)
    content["summary"] = "Platform engineer. Led a team of 12 engineers at Example Corp."
    for revision in (1, 2):
        response = api.request(
            "PUT",
            f"/api/documents/{base_id}/draft",
            token=token,
            json=content,
            headers={"If-Match": str(revision)},
        )
        assert response.status_code == 200, response.text
        notes = response.json()["notes"]
        assert any(note.get("field") == "summary" for note in notes), notes
    with factory() as db:
        generations = db.scalars(sa.select(DocumentGeneration.status)).all()
        assert GenerationStatus.running not in generations
