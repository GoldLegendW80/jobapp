"""Interview preparation through the app, two e2e people: the read before and after Prepare,
Prepare refused without the consent then started (202, the run queued), a second click while it
runs (409), the Practice message (empty 422, started 202), the stories kept (none chosen 422), a
hand edit with the Debrief (a version, notes on the claims only), the posting's CV and letter
list without it, and B's posting and preparation answering 404 to A everywhere."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind
from app.documents import consent, store
from app.documents.models import Author, Kind
from tests.agents.conftest import API_KEY, new_key
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-prep-alex@example.org"
BLAKE = "e2e-docs-prep-blake@example.org"
STORY = {
    "title": "The deployment platform",
    "action": "I built the deployment platform at Example Corp.",
}


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> dict[str, Any]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    with factory() as db:
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text, status) VALUES "
                "(:u, :s, 'Lumen Example', 'Platform engineer', 'Run our platform.', "
                "'interview') RETURNING id"
            ),
            {"u": user.id, "s": f"lumen-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        credentials.save(
            db, Keyring.from_text(new_key()), user.id, CredentialKind.anthropic_api_key, API_KEY,
            clock(),
        )  # fmt: skip
        db.commit()
    return {"token": api.login_token(email), "user": user.id, "posting": posting}


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, ALEX, clock)


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, BLAKE, clock)


def _url(who: dict[str, Any]) -> str:
    return f"/api/jobs/{who['posting']}/interview-prep"


def _consent(factory: sessionmaker[Session], who: dict[str, Any], clock: Any) -> None:
    with factory() as db:
        consent.record(db, who["user"], clock())
        db.commit()


def _prepared(api: Api, who: dict[str, Any]) -> str:
    started = api.request("POST", _url(who), token=who["token"])
    assert started.status_code == 202, started.text
    return str(started.json()["document_id"])


def test_prepare_starts_on_the_click_once_the_consent_is_there(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session], clock: Any,
    queue: RecordingQueue,
) -> None:  # fmt: skip
    before = api.get(_url(alex), token=alex["token"])
    assert before.status_code == 200, before.text
    assert before.headers["cache-control"] == "private, no-store"
    assert before.json()["document"] is None
    refused = api.request("POST", _url(alex), token=alex["token"])
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Confirm what is sent")
    assert api.get(_url(alex), token=alex["token"]).json()["document"] is None  # nothing stored
    _consent(factory, alex, clock)
    document = _prepared(api, alex)
    assert len(queue.jobs) == 1
    again = api.request("POST", _url(alex), token=alex["token"])
    assert again.status_code == 409
    read = api.get(_url(alex), token=alex["token"]).json()
    assert read["document"]["id"] == document
    assert read["writing"]["status"] == "running"
    assert read["practice"] == []
    assert read["practice_open"] is False
    listed = api.get(f"/api/jobs/{alex['posting']}/documents", token=alex["token"]).json()
    assert listed["documents"] == []  # the CV and letter list is not the preparation's


def test_practice_and_stories_refuse_what_they_cannot_take(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    _consent(factory, alex, clock)
    document = _prepared(api, alex)
    empty = api.request(
        "POST", f"/api/documents/{document}/practice", token=alex["token"], json={"text": "  "}
    )
    assert empty.status_code == 422
    busy = api.request(
        "POST", f"/api/documents/{document}/practice", token=alex["token"], json={"text": "Go"}
    )
    assert busy.status_code == 409  # the preparation is still being written
    none = api.request(
        "POST", f"/api/documents/{document}/stories", token=alex["token"], json={"indexes": [0]}
    )
    assert none.status_code == 422


def test_a_debrief_by_hand_makes_a_version_with_notes_on_the_claims_only(
    api: Api, alex: dict[str, Any], factory: sessionmaker[Session], clock: Any
) -> None:
    with factory() as db:
        made = store.get_or_create(
            db, alex["user"], alex["posting"], Kind.interview_prep, at=clock()
        )
        db.commit()
        document, revision = made.id, made.draft_revision
    content = {
        "schema": 1,
        "expected": [{"question": "You ask for 10 years of Go: why?", "basis": "posting"}],
        "stories": [{**STORY, "result": "Costs fell by 61%."}],
        "debrief": [{"on": "Round one", "asked": "They asked about 14 services."}],
    }
    saved = api.request(
        "PUT",
        f"/api/documents/{document}/draft",
        token=alex["token"],
        json=content,
        headers={"If-Match": str(revision)},
    )
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["draft"]["stories"][0]["result"] == "Costs fell by 61%."  # never refused
    claims = {n["field"] for n in out["notes"] if n["level"] == "claim"}
    assert claims == {"stories.0.result"}
    assert out["latest"]["number"] == 1
    read = api.get(_url(alex), token=alex["token"]).json()
    assert read["document"]["versions"] == 1
    assert read["document"]["draft"]["debrief"][0]["asked"] == "They asked about 14 services."
    kept = api.request(
        "POST", f"/api/documents/{document}/stories", token=alex["token"], json={"indexes": [0]}
    )
    assert kept.status_code == 200, kept.text
    proposal = kept.json()["proposal_id"]
    assert api.get(_url(alex), token=alex["token"]).json()["stories_proposal"] == proposal
    with factory() as db:
        assert store.get(db, alex["user"], document).draft_written_by is Author.user


def test_bs_preparation_answers_404_to_a(
    api: Api, alex: dict[str, Any], blake: dict[str, Any], factory: sessionmaker[Session],
    clock: Any,
) -> None:  # fmt: skip
    _consent(factory, blake, clock)
    document = _prepared(api, blake)
    token = alex["token"]
    assert api.get(_url(blake), token=token).status_code == 404
    assert api.request("POST", _url(blake), token=token).status_code == 404
    practice = api.request(
        "POST", f"/api/documents/{document}/practice", token=token, json={"text": "Hello"}
    )
    assert practice.status_code == 404
    stories = api.request(
        "POST", f"/api/documents/{document}/stories", token=token, json={"indexes": [0]}
    )
    assert stories.status_code == 404
    assert api.get(f"/api/documents/{document}", token=token).status_code == 404
