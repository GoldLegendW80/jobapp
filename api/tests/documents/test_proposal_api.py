"""Issue #485: a proposal through the documents api, two e2e people. Read, edited by hand (a
version each save, notes computed, never refused for what it says, the hard limits 422),
restored and approved like any document; listed with its posting's documents; refused where
only a CV and a letter make sense (the template and language switch, "Render", the template
thumbnail, "Write now", the chat); and B's proposal answers 404 to A everywhere."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.documents import proposal
from app.documents.router import NOT_RENDERED
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-proposal-alex@example.org"
BLAKE = "e2e-docs-proposal-blake@example.org"
CONTACT = "jobs@lumen.example"
BODY = (
    "Hello,\n\nI run the deployment platform of the payments team at Example Corp.\n\n"
    "I would be glad to talk about how I can help."
)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> dict[str, Any]:
    """A person with a profile, a posting and its proposal (no consent: its writing waits)."""
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    with factory() as db:
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text) VALUES "
                "(:u, :s, 'Lumen Example', 'Platform engineer', 'Write to us.') RETURNING id"
            ),
            {"u": user.id, "s": f"lumen-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        made = proposal.create(db, user.id, posting, contact=CONTACT, at=clock())
        db.commit()
        document = made.document.id
    return {
        "token": api.login_token(email),
        "user": user.id,
        "posting": posting,
        "document": document,
    }


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, ALEX, clock)


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Any:
    return _person(factory, api, BLAKE, clock)


def _save(api: Api, who: dict[str, Any], content: dict[str, Any], revision: int) -> Any:
    return api.request(
        "PUT",
        f"/api/documents/{who['document']}/draft",
        token=who["token"],
        json=content,
        headers={"If-Match": str(revision)},
    )


def _read(api: Api, who: dict[str, Any]) -> dict[str, Any]:
    answer = api.get(f"/api/documents/{who['document']}", token=who["token"])
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_proposal_is_read_and_listed_with_its_postings_documents(
    api: Api, alex: dict[str, Any]
) -> None:
    read = _read(api, alex)
    assert read["kind"] == "proposal"
    assert read["draft"]["to"] == CONTACT
    assert read["render"]["state"] == "idle"
    listed = api.get(f"/api/jobs/{alex['posting']}/documents", token=alex["token"]).json()
    [only] = listed["documents"]
    assert only["id"] == str(alex["document"])
    assert only["kind"] == "proposal"
    assert only["generation"]["status"] == "waiting"
    assert only["generation"]["wait_reason"] == "consent"


def test_a_hand_edit_makes_a_version_with_its_notes_and_queues_no_render(
    api: Api, alex: dict[str, Any], queue: RecordingQueue
) -> None:
    revision = _read(api, alex)["draft_revision"]
    content = {
        "schema": 1,
        "to": CONTACT,
        "subject": "A platform for your team",
        "body": BODY + " At Globex Corporation I led the data platform.",
    }
    saved = _save(api, alex, content, revision)
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["draft"]["body"].endswith("data platform.")  # never refused for what it says
    assert [n["code"] for n in out["notes"] if n["level"] == "claim"] == ["employer_not_in_profile"]
    assert out["render"]["state"] == "idle"
    assert out["latest"]["number"] == 1
    assert out["latest"]["origin"] == "edited"
    assert queue.jobs == []
    again = _save(api, alex, content, out["draft_revision"])  # the same text: the same version
    assert again.json()["latest"]["number"] == 1
    changed = _save(api, alex, {**content, "body": BODY}, again.json()["draft_revision"])
    assert changed.json()["latest"]["number"] == 2
    assert changed.json()["notes"] == []
    listed = api.get(f"/api/documents/{alex['document']}/versions", token=alex["token"]).json()
    assert [v["number"] for v in listed] == [2, 1]
    assert all(not v["has_pdf"] for v in listed)


def test_a_restore_makes_a_version_and_an_approval_marks_one(
    api: Api, alex: dict[str, Any], queue: RecordingQueue
) -> None:
    revision = _read(api, alex)["draft_revision"]
    first = _save(api, alex, {"to": CONTACT, "subject": "One", "body": BODY}, revision).json()
    second = _save(
        api, alex, {"to": CONTACT, "subject": "Two", "body": BODY}, first["draft_revision"]
    ).json()
    restored = api.post(
        f"/api/documents/{alex['document']}/versions/1/restore", token=alex["token"]
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["draft"]["subject"] == "One"
    assert restored.json()["latest"]["number"] == 3
    assert restored.json()["latest"]["origin"] == "restored"
    assert second["latest"]["number"] == 2
    approved = api.post(
        f"/api/documents/{alex['document']}/versions/3/approve", token=alex["token"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_at"] is not None
    assert queue.jobs == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"body": " ".join(["word"] * 181)}, "too_many_words"),
        ({"to": "jobs@lumen.example, hr@lumen.example"}, "bad_address"),
        ({"paragraphs": ["A letter's"]}, "unknown_field"),
    ],
)
def test_a_draft_over_the_hard_limits_is_422(
    api: Api, alex: dict[str, Any], change: dict[str, Any], code: str
) -> None:
    revision = _read(api, alex)["draft_revision"]
    saved = _save(api, alex, {"to": CONTACT, "subject": "S", "body": BODY, **change}, revision)
    assert saved.status_code == 422
    assert [e["code"] for e in saved.json()["detail"]] == [code]
    assert _read(api, alex)["draft_revision"] == revision


@pytest.mark.parametrize("change", [{"template_slug": "classic"}, {"language": "fr"}])
def test_the_template_and_language_switch_refuse_a_proposal(
    api: Api, alex: dict[str, Any], change: dict[str, Any]
) -> None:
    answer = api.request(
        "PATCH", f"/api/documents/{alex['document']}", token=alex["token"], json=change
    )
    assert answer.status_code == 422
    assert answer.json()["detail"] == NOT_RENDERED


def test_render_refuses_a_proposal(api: Api, alex: dict[str, Any], queue: RecordingQueue) -> None:
    answer = api.post(f"/api/documents/{alex['document']}/render", token=alex["token"])
    assert answer.status_code == 422
    assert answer.json()["detail"] == NOT_RENDERED
    assert queue.jobs == []
    assert (
        api.get(
            f"/api/documents/{alex['document']}/versions/1/pdf", token=alex["token"]
        ).status_code
        == 404
    )


def test_the_template_thumbnail_and_write_now_know_no_proposal(
    api: Api, alex: dict[str, Any]
) -> None:
    thumbnail = api.get(
        "/api/document-templates/classic/thumbnail?kind=proposal", token=alex["token"]
    )
    assert thumbnail.status_code == 422
    write = api.post(
        f"/api/jobs/{alex['posting']}/documents/write",
        token=alex["token"],
        json={"kinds": ["proposal"]},
    )
    assert write.status_code == 422


def test_the_chat_refuses_a_proposal(api: Api, alex: dict[str, Any]) -> None:
    answer = api.post(
        f"/api/documents/{alex['document']}/messages",
        token=alex["token"],
        json={"text": "Make it shorter."},
    )
    assert answer.status_code == 422
    assert "proposal" in answer.json()["detail"]


def test_bs_proposal_answers_404_to_a(
    api: Api, alex: dict[str, Any], blake: dict[str, Any]
) -> None:
    theirs = blake["document"]
    token = alex["token"]
    assert api.get(f"/api/documents/{theirs}", token=token).status_code == 404
    assert api.get(f"/api/documents/{theirs}/versions", token=token).status_code == 404
    assert api.get(f"/api/jobs/{blake['posting']}/documents", token=token).status_code == 404
    saved = api.request(
        "PUT",
        f"/api/documents/{theirs}/draft",
        token=token,
        json={"to": CONTACT, "subject": "Mine now", "body": BODY},
        headers={"If-Match": "2"},
    )
    assert saved.status_code == 404
    assert api.post(f"/api/documents/{theirs}/versions/1/restore", token=token).status_code == 404
    assert api.post(f"/api/documents/{theirs}/render", token=token).status_code == 404
    assert _read(api, blake)["draft"].get("subject", "") == ""
