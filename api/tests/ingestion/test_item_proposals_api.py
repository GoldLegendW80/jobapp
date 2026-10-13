"""Profile items through /api/proposals (real Postgres): the kind filter of the list, accept
with its 404, 409 (not pending, stale, profile conflict, invalid) and 422 (the user's own value,
a conflict left unchosen), reject, and isolation between users. No page uses these yet: the
review page of the ingestion module will. Synthetic data only."""

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import ProposalStatus
from app.ingestion import items
from tests.agents.run_helpers import add_job
from tests.identity.api_helpers import Api
from tests.ingestion.conftest import ALICE
from tests.ingestion.helpers import user_id
from tests.ingestion.test_item_proposals import (
    BLOCK,
    GERMAN,
    cite,
    made,
    raw,
    revision,
    row,
    save,
    stored,
    tick,
)
from tests.ingestion.test_items import MARKER
from tests.profile.conftest import full

LIST = "/api/proposals"


def accept(api: Api, token: str | None, pid: Any, **body: Any) -> Any:
    return api.post(f"{LIST}/{pid}/accept", token=token, json=body)


def reject(api: Api, token: str | None, pid: Any) -> Any:
    return api.post(f"{LIST}/{pid}/reject", token=token)


@pytest.fixture
def me(factory: sessionmaker[Session], alice: str) -> uuid.UUID:
    return user_id(factory, ALICE)


def test_the_list_filters_profile_items(
    api: Api, alice: str, bob: str, me: uuid.UUID, factory: sessionmaker[Session]
) -> None:
    job = add_job(factory, me, "Acme", "Engineer")
    with factory() as db, db.begin():
        note = proposals.create(
            db,
            me,
            target=proposals.TARGET_JOB,
            target_id=job,
            kind="job_note",
            payload={"text": "A note."},
            run_id=None,
            now=tick(),
        ).id
    pid = made(factory, me, "facts", "blocks", BLOCK)

    def listed(token: str, query: str = "") -> list[dict[str, Any]]:
        response = api.get(f"{LIST}{query}", token=token)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        found: list[dict[str, Any]] = response.json()["items"]
        return found

    (item,) = listed(alice, "?kind=profile_item")
    assert (item["id"], item["kind"], item["target"], item["status"]) == (
        str(pid),
        "profile_item",
        "profile",
        "pending",
    )
    assert (item["target_id"], item["run_id"], item["edited_value"]) == (None, None, None)
    assert item["payload"]["section"] == "facts"
    assert item["payload"]["key"] == "blocks:side projects"
    assert item["payload"]["value"] == BLOCK
    assert [found["id"] for found in listed(alice, "?target=profile")] == [str(pid)]
    assert [found["id"] for found in listed(alice, "?kind=job_note")] == [str(note)]
    assert [found["id"] for found in listed(alice, "?kind=profile_item&kind=job_note")] == [
        str(pid),
        str(note),
    ]
    assert listed(alice, "?kind=profile_item&status=accepted") == []
    assert listed(bob) == []
    assert api.get(f"{LIST}?kind=profile_item").status_code == 401


def test_accept_and_reject_a_profile_item(
    api: Api, alice: str, bob: str, me: uuid.UUID, factory: sessionmaker[Session]
) -> None:
    pid = made(factory, me, "facts", "blocks", BLOCK)
    other = made(factory, me, "identity", "languages", GERMAN)
    assert accept(api, None, pid).status_code == 401
    assert (
        api.request("POST", f"{LIST}/{pid}/accept", token=alice, csrf=False, json={}).status_code
        == 403
    )
    for path in (pid, uuid.uuid4(), "not-an-id"):
        assert accept(api, bob, path).status_code == 404
        assert reject(api, bob, path).status_code == 404
    assert revision(factory, me) == 0

    response = accept(api, alice, pid)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["decided_by"]) == ("accepted", "user")
    assert body["payload"]["applied_revision"] == 1
    assert stored(factory, me, "facts")["blocks"] == [BLOCK]

    again = accept(api, alice, pid)
    assert again.status_code == 409
    assert (again.json()["reason"], again.json()["status"]) == ("not_pending", "accepted")
    assert reject(api, alice, pid).status_code == 409

    rejected = reject(api, alice, other)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert (revision(factory, me), stored(factory, me, "identity")["languages"]) == (1, [])


def test_the_409_answers_of_a_profile_item(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session]
) -> None:
    save(factory, me, "identity", full("identity"))
    level = made(factory, me, "identity", "languages", {"name": "English", "level": "C2"})
    typed = full("identity")
    typed["languages"][1]["level"] = "Typed by the user"
    current = save(factory, me, "identity", typed)
    stale = accept(api, alice, level)
    assert stale.status_code == 409
    assert stale.headers["cache-control"] == "no-store"
    assert stale.json() == {
        "detail": (
            "The profile changed here since this was proposed. "
            "Compare the two values and accept again."
        ),
        "reason": "stale",
        "status": None,
        "profile_revision": current,
    }
    assert accept(api, alice, level, profile_revision=current - 1).status_code == 409
    assert stored(factory, me, "identity")["languages"][1]["level"] == "Typed by the user"
    assert accept(api, alice, level, profile_revision=current).status_code == 200
    assert stored(factory, me, "identity")["languages"][1]["level"] == "C2"

    link = made(factory, me, "identity", "links", {"label": MARKER, "url": "https://x.example"})
    links = [{"label": f"Link {n}", "url": f"https://example.org/{n}"} for n in range(10)]
    save(factory, me, "identity", {**full("identity"), "links": links})
    conflict = accept(api, alice, link)
    assert conflict.status_code == 409
    assert (conflict.json()["reason"], conflict.json()["status"]) == ("profile_conflict", None)
    assert conflict.json()["detail"].startswith("The profile cannot take this as it is: ")
    assert MARKER not in conflict.text
    assert row(factory, link).status is ProposalStatus.pending

    broken = raw(
        factory, me, {"section": "nowhere", "key": "full_name", "action": "add", "value": MARKER}
    )
    invalid = accept(api, alice, broken)
    assert invalid.status_code == 409
    assert invalid.json() == {
        "detail": "This value no longer fits the profile: this is not a section of the profile.",
        "reason": "invalid",
        "status": "invalid",
        "profile_revision": None,
    }
    assert row(factory, broken).status is ProposalStatus.invalid  # committed with the answer
    assert accept(api, alice, broken).json()["reason"] == "not_pending"


def test_the_422_answers_of_a_profile_item(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session]
) -> None:
    gate = made(factory, me, "skills", "gates", {"name": "phd", "terms": ["phd"]})
    refused = accept(
        api, alice, gate, edited_value={"name": "phd", "terms": [MARKER], "penalty": 1000}
    )
    assert refused.status_code == 422
    assert "penalty" in refused.text
    assert MARKER not in refused.text
    assert accept(api, alice, gate, edited_value=None).status_code == 422
    assert accept(api, alice, gate, anything="else").status_code == 422
    assert (row(factory, gate).status, revision(factory, me)) == (ProposalStatus.pending, 0)
    chosen = {"name": "phd", "terms": ["phd"], "penalty": 60}
    done = accept(api, alice, gate, edited_value=chosen)
    assert done.status_code == 200, done.text
    assert done.json()["edited_value"] == chosen
    assert stored(factory, me, "skills")["gates"][0]["penalty"] == 60

    other = items.build("identity", "languages", "add", {**GERMAN, "level": "C1"})
    both = made(factory, me, "identity", "languages", GERMAN, conflict=[(other, [cite(3)])])
    unchosen = accept(api, alice, both)
    assert unchosen.status_code == 422
    assert "choose one" in unchosen.text
    assert accept(api, alice, both, edited_value=other.value).status_code == 200
    assert stored(factory, me, "identity")["languages"] == [other.value]
