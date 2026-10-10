"""/api/proposals through the app (real Postgres): default deny and CSRF, the list and its
filters, accept (with and without an edited value, every 409 and 422), reject, isolation, the
accepted change visible in the job's events; and the A3 additions to /api/agents/runs (triage
needs a job, the job and agent filters, the job's label)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import ProposalStatus
from tests.agents.run_helpers import add_job, new_run
from tests.agents.test_proposals_service import NOTE, STATUS, propose
from tests.agents.test_runs_api import RecordingQueue, save_key, user_id
from tests.identity.api_helpers import Api

LIST = "/api/proposals"


def accept(api: Api, token: str, pid: uuid.UUID | str, **body: Any) -> Any:
    return api.post(f"{LIST}/{pid}/accept", token=token, json=body)


def reject(api: Api, token: str, pid: uuid.UUID | str) -> Any:
    return api.post(f"{LIST}/{pid}/reject", token=token)


def job_detail(api: Api, token: str, job: uuid.UUID) -> dict[str, Any]:
    response = api.get(f"/api/jobs/{job}", token=token)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.fixture
def setup(api: Api, alice: str, factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    """Alice's user id and one of her jobs."""
    me = user_id(api, alice)
    return me, add_job(factory, me, "Mistral AI", "Applied AI Engineer")


def test_everything_needs_a_session_and_csrf(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    pid = propose(factory, me, job)
    assert api.get(LIST).status_code == 401
    assert api.post(f"{LIST}/{pid}/accept", json={}).status_code == 401
    assert api.post(f"{LIST}/{pid}/reject").status_code == 401
    for path in (f"{LIST}/{pid}/accept", f"{LIST}/{pid}/reject"):
        response = api.request("POST", path, token=alice, csrf=False, json={})
        assert response.status_code == 403, path
    assert api.get(LIST, token=alice).status_code == 200


def test_list_and_filters(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    run = new_run(factory, me, job_id=job, agent="triage", data={})
    note = propose(factory, me, job, run=run)
    status = propose(factory, me, job, STATUS, run=run)
    other = propose(factory, me, add_job(factory, me, "Other", "Role"))

    def ids(query: str = "") -> list[str]:
        response = api.get(f"{LIST}{query}", token=alice)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        return [item["id"] for item in response.json()["items"]]

    assert ids() == [str(other), str(status), str(note)]
    assert ids(f"?kind={NOTE}") == [str(other), str(note)]
    assert ids(f"?kind={STATUS}") == [str(status)]
    assert ids(f"?kind={NOTE}&kind={STATUS}&target_id={job}") == [str(status), str(note)]
    assert ids(f"?target=job&target_id={job}&status=pending") == [str(status), str(note)]
    assert ids(f"?run_id={run}") == [str(status), str(note)]
    assert ids("?status=accepted") == []
    assert ids("?limit=1") == [str(other)]
    item = api.get(f"{LIST}?kind={NOTE}&target_id={job}", token=alice).json()["items"][0]
    assert item == {
        "id": str(note),
        "run_id": str(run),
        "target": "job",
        "target_id": str(job),
        "kind": NOTE,
        "payload": {"text": "Strong fit."},
        "status": "pending",
        "decided_by": None,
        "decided_at": None,
        "edited_value": None,
        "reason": None,
        "created_at": item["created_at"],
    }
    for bad in ("?status=maybe", "?limit=0", "?limit=201", "?target_id=nope", "?run_id=1"):
        assert api.get(f"{LIST}{bad}", token=alice).status_code == 422, bad


def test_accept_note_shows_in_the_job(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    pid = propose(factory, me, job)
    version = job_detail(api, alice, job)["updated_at"]
    response = accept(api, alice, pid, updated_at=version)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "accepted"
    assert body["decided_by"] == "user"
    assert body["decided_at"] is not None
    detail = job_detail(api, alice, job)
    assert detail["notes"] == "Strong fit."
    assert detail["updated_at"] != version
    note = next(event for event in detail["events"] if event["kind"] == "note")
    assert note["data"]["via"] == "agent"
    assert note["data"]["proposal_id"] == str(pid)


def test_accept_edited_note_and_status(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    note = propose(factory, me, job)
    response = accept(api, alice, note, edited_value="My words, not the agent's.")
    assert response.status_code == 200, response.text
    assert response.json()["edited_value"] == "My words, not the agent's."
    payload = {"status": "skipped", "from": "found", "reason": "r"}
    status = propose(factory, me, job, STATUS, payload)
    response = accept(api, alice, status)
    assert response.status_code == 200, response.text
    detail = job_detail(api, alice, job)
    assert detail["status"] == "skipped"
    assert detail["notes"] == "My words, not the agent's."
    change = next(event for event in detail["events"] if event["kind"] == "status")
    assert change["data"] == {
        "from": "found",
        "to": "skipped",
        "via": "agent",
        "proposal_id": str(status),
    }


@pytest.mark.parametrize(
    ("kind", "value"),
    [(NOTE, ""), (NOTE, None), (NOTE, {"text": "x"}), (NOTE, "x" * 4001), (STATUS, "maybe")],
)
def test_edited_value_validation_is_422(
    api: Api,
    alice: str,
    setup: tuple[uuid.UUID, uuid.UUID],
    factory: sessionmaker[Session],
    kind: str,
    value: Any,
) -> None:
    me, job = setup
    pid = propose(factory, me, job, kind)
    response = accept(api, alice, pid, edited_value=value)
    assert response.status_code == 422, response.text
    assert response.json()["detail"].startswith("edited_value: ")
    assert api.get(f"{LIST}?status=pending", token=alice).json()["items"][0]["id"] == str(pid)


def test_bad_bodies(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    pid = propose(factory, me, job)
    assert accept(api, alice, pid, surprise=1).status_code == 422
    assert accept(api, alice, pid, updated_at="yesterday").status_code == 422


def test_stale_job_version_is_409(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    pid = propose(factory, me, job, STATUS)
    seen = job_detail(api, alice, job)["updated_at"]
    patched = api.request(
        "PATCH", f"/api/jobs/{job}", token=alice, json={"notes": "edited elsewhere"}
    )
    assert patched.status_code == 200, patched.text
    response = accept(api, alice, pid, updated_at=seen)
    assert response.status_code == 409
    body = response.json()
    assert body["reason"] == "stale"
    assert body["updated_at"] == patched.json()["updated_at"]
    assert job_detail(api, alice, job)["status"] == "found"
    retry = accept(api, alice, pid, updated_at=body["updated_at"])
    assert retry.status_code == 200, retry.text


def test_decided_and_gone_are_409(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    pid = propose(factory, me, job)
    assert reject(api, alice, pid).json()["status"] == "rejected"
    for again in (accept(api, alice, pid), reject(api, alice, pid)):
        assert again.status_code == 409
        assert again.json() == {
            "detail": "This proposal was already decided: it is rejected.",
            "reason": "not_pending",
            "status": "rejected",
            "updated_at": None,
        }
    superseded = propose(factory, me, job)
    propose(factory, me, job)
    assert accept(api, alice, superseded).json()["status"] == "superseded"
    gone_job = add_job(factory, me, "Gone", "Soon")
    gone = propose(factory, me, gone_job)
    from app.jobs.models import Job

    with factory() as db, db.begin():
        db.query(Job).filter(Job.id == gone_job).delete()
    response = accept(api, alice, gone)
    assert response.status_code == 409
    assert response.json()["reason"] == "target_gone"
    listed = api.get(f"{LIST}?status=invalid", token=alice).json()["items"]
    assert [item["id"] for item in listed] == [str(gone)]
    assert listed[0]["decided_by"] == "system"
    assert listed[0]["reason"] == "The job no longer exists."


def test_isolation(
    api: Api,
    alice: str,
    bob: str,
    setup: tuple[uuid.UUID, uuid.UUID],
    factory: sessionmaker[Session],
) -> None:
    me, job = setup
    pid = propose(factory, me, job)
    assert api.get(LIST, token=bob).json() == {"items": []}
    assert api.get(f"{LIST}?target_id={job}", token=bob).json() == {"items": []}
    for response in (accept(api, bob, pid), reject(api, bob, pid)):
        assert response.status_code == 404
        assert response.json()["detail"] == "Proposal not found."
    for wrong in (uuid.uuid4(), "not-a-uuid"):
        assert accept(api, alice, wrong).status_code == 404
        assert reject(api, alice, wrong).status_code == 404
    assert api.get(f"{LIST}?status=pending", token=alice).json()["items"][0]["id"] == str(pid)
    assert job_detail(api, alice, job)["notes"] == ""


# --- /api/agents/runs additions -----------------------------------------------------------------


def test_triage_needs_a_job(
    api: Api, alice: str, recorded: RecordingQueue, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, job = setup
    save_key(api, alice)
    response = api.post("/api/agents/runs", token=alice, json={"agent": "triage", "input": {}})
    assert response.status_code == 422
    assert response.json()["detail"] == "job_id: this agent works on a job; name one."
    bad = api.post(
        "/api/agents/runs",
        token=alice,
        json={"agent": "triage", "job_id": str(job), "input": {"message": "hi"}},
    )
    assert bad.status_code == 422
    started = api.post(
        "/api/agents/runs", token=alice, json={"agent": "triage", "job_id": str(job), "input": {}}
    )
    assert started.status_code == 202, started.text
    assert started.json()["job"] == {
        "id": str(job),
        "company": "Mistral AI",
        "title": "Applied AI Engineer",
    }
    assert len(recorded.jobs) == 1


def test_runs_list_filters_and_job_label(
    api: Api, alice: str, setup: tuple[uuid.UUID, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    me, job = setup
    triage = new_run(factory, me, job_id=job, agent="triage", data={})
    echo_on_job = new_run(factory, me, job_id=job)
    alone = new_run(factory, me)

    def ids(query: str) -> list[str]:
        response = api.get(f"/api/agents/runs{query}", token=alice)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert set(ids("")) == {str(triage), str(echo_on_job), str(alone)}
    assert set(ids(f"?job_id={job}")) == {str(triage), str(echo_on_job)}
    assert ids(f"?job_id={job}&agent=triage") == [str(triage)]
    assert ids("?agent=nothing") == []
    items = {item["id"]: item for item in api.get("/api/agents/runs", token=alice).json()["items"]}
    assert items[str(triage)]["job"] == {
        "id": str(job),
        "company": "Mistral AI",
        "title": "Applied AI Engineer",
    }
    assert items[str(alone)]["job"] is None
    detail = api.get(f"/api/agents/runs/{triage}", token=alice).json()
    assert detail["job"]["title"] == "Applied AI Engineer"


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def test_statuses_cover_the_spec() -> None:
    assert [s.value for s in ProposalStatus] == [
        "pending",
        "accepted",
        "rejected",
        "superseded",
        "invalid",
        "undone",
    ]
