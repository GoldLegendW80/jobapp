"""D9: the base CVs through the app, two e2e people. The list and its default (made twice,
deleted with others and last), a build from the profile (waiting for a credential alone, then
started; D30 removed the consent and its route), a build from an empty profile (a skeleton with
its note), a copy (and a copy of another person's, 404), the 11th refused with its sentence, a
delete that leaves the posting CVs written from it, an upload of an unknown file (404; D24's own
tests are in test_base_upload_api.py), the model the estimates are priced with, and a claim
added by hand to a base CV that stays a note at every save."""

from __future__ import annotations

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
from app.documents import base_router, estimate, store, writer_base
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


# --- no consent ----------------------------------------------------------------------------------


def test_the_documents_consent_has_no_route_any_more() -> None:
    """D30: the owner removed the documents consent; nothing asks it or records it."""
    paths = {getattr(route, "path", "") for route in base_router.router.routes}
    assert not any(path.endswith("/consent") for path in paths)


def test_the_estimates_are_priced_with_the_writers_model_of_the_credential() -> None:
    assert estimate.model_for(None) is None
    assert estimate.model_for(CredentialKind.anthropic_api_key) == "claude-sonnet-5-5"
    assert estimate.model_for(CredentialKind.claude_subscription) == "claude-opus-5-5"


# --- building from the profile ------------------------------------------------------------------


def test_a_build_waits_for_the_credential_alone_then_starts(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    response = _build(api, token)
    assert response.status_code == 202, response.text
    built = response.json()["base_cv"]
    assert built["source"] == "built"
    assert built["template_slug"] == "band"
    assert built["is_default"] is True
    assert built["name"] == "My CV"
    assert built["generation"]["status"] == "waiting"
    assert built["generation"]["wait_reason"] == "credential"

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


def test_a_build_with_a_credential_and_no_consent_row_starts_its_run_at_once(
    api: Api, alex: Any, factory: sessionmaker[Session], clock: Any, queue: RecordingQueue
) -> None:
    token, user = alex
    _credential(factory, user, clock)
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
            template_slug="photo-columns",
        )
        db.commit()
        source_id, draft = str(source.id), source.draft
    response = api.request("POST", BASES, token=token, json={"from": "copy", "copy_of": source_id})
    assert response.status_code == 201, response.text
    copy = response.json()["base_cv"]
    assert copy["name"] == "Copy of Original"
    assert copy["template_slug"] == "photo-columns"
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


def test_from_an_upload_of_an_unknown_file_answers_404(api: Api, alex: Any) -> None:
    token, _ = alex
    response = api.request(
        "POST",
        BASES,
        token=token,
        json={"from": "upload", "ingestion_document_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == base_router.UPLOAD_NOT_FOUND


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
