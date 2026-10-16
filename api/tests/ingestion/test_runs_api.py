"""The routes of an ingestion run (ingestion G10) against real Postgres: the start with its
consent, its echo and its refusals, the read, cancel, resume and continue, and the isolation
between users. The queue of these tests records what it is given (conftest); a step's end is
written as the runner writes it (`engine_helpers`)."""

import json
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.models import AgentRun, CredentialKind
from app.agents.models import RunStatus as AgentStatus
from app.identity.deps import UNAUTHORIZED
from app.ingestion import engine, estimate_router, runs_router
from app.ingestion.models import TextStatus
from app.ingestion.runs_models import IngestionRun
from tests.identity.api_helpers import Api
from tests.ingestion import engine_helpers as eh
from tests.ingestion.conftest import ALICE, BOB, RecordingQueue
from tests.ingestion.helpers import delete, rows, set_columns, uploaded
from tests.ingestion.test_estimate_api import CONSENT, ESTIMATE, credential

RUNS = "/api/ingestion/runs"
MARKER = "zebra-quartz-0451"


def document(api: Api, token: str, number: int) -> str:
    """A small made-up text document that holds a marker no answer may repeat."""
    text = f"{MARKER} {eh.text_of(number)}"
    return str(uploaded(api, token, text.encode(), f"notes-{number}.md")["id"])


def consent(api: Api, token: str) -> None:
    version = estimate_router.CONSENT_VERSION
    assert api.post(CONSENT, token=token, json={"version": version}).status_code == 200


def quote_id(api: Api, token: str, ids: list[str], **more: Any) -> str:
    response = api.post(ESTIMATE, token=token, json={"document_ids": ids, **more})
    assert response.status_code == 200, response.text
    return str(response.json()["quote_id"])


def start(api: Api, token: str | None, ids: list[str], **more: Any) -> httpx.Response:
    body = {"document_ids": ids, **more}
    if "quote_id" not in body and token is not None:
        reread = {key: value for key, value in more.items() if key == "reread_document_ids"}
        body["quote_id"] = quote_id(api, token, ids, **reread)
    return api.post(RUNS, token=token, json=body)


def runs(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(db.scalar(sa.select(sa.func.count()).select_from(IngestionRun)) or 0)


@pytest.fixture
def ready(factory: sessionmaker[Session], api: Api, alice: str) -> list[str]:
    """Alice has a credential, has consented and has two documents."""
    credential(factory, ALICE)
    consent(api, alice)
    return [document(api, alice, 0), document(api, alice, 1)]


def started(api: Api, token: str, ids: list[str]) -> dict[str, Any]:
    response = start(api, token, ids)
    assert response.status_code == 201, response.text
    return dict(response.json())


def end_step(app: FastAPI, factory: sessionmaker[Session], run: dict[str, Any], **how: Any) -> None:
    eh.finish_step(factory, uuid.UUID(run["id"]), app.state.settings, **how)


# --- signed out, and other users --------------------------------------------------------------


def test_signed_out_answers_401_on_every_route(api: Api) -> None:
    some = str(uuid.uuid4())
    calls = [
        api.post(RUNS, json={"document_ids": [some], "quote_id": "0" * 64}),
        api.get(RUNS),
        api.get(f"{RUNS}/{some}"),
        api.post(f"{RUNS}/{some}/cancel"),
        api.post(f"{RUNS}/{some}/resume"),
        api.post(f"{RUNS}/{some}/continue", json={"adds": 1}),
    ]
    for response in calls:
        assert response.status_code == 401, response.request.url
        assert response.json() == {"detail": UNAUTHORIZED}


def test_user_b_reading_cancelling_resuming_or_continuing_as_run_answers_404(
    factory: sessionmaker[Session], api: Api, alice: str, bob: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    unknown = str(uuid.uuid4())
    for run_id in (run["id"], unknown, "not-an-id"):
        answers = [
            api.get(f"{RUNS}/{run_id}", token=bob),
            api.post(f"{RUNS}/{run_id}/cancel", token=bob),
            api.post(f"{RUNS}/{run_id}/resume", token=bob),
            api.post(f"{RUNS}/{run_id}/continue", token=bob, json={"adds": 1}),
        ]
        # Another user's run answers exactly like one that does not exist.
        assert [(one.status_code, one.json()) for one in answers] == [
            (404, {"detail": runs_router.NOT_FOUND})
        ] * 4
    assert api.get(RUNS, token=bob).json() == {"runs": [], "active_run_id": None}
    mine = api.get(f"{RUNS}/{run['id']}", token=alice).json()
    assert (mine["status"], mine["cancel_requested"]) == ("running", False)


def test_another_users_document_cannot_start_a_run(
    factory: sessionmaker[Session], api: Api, alice: str, bob: str, ready: list[str]
) -> None:
    credential(factory, BOB)
    consent(api, bob)
    response = api.post(RUNS, token=bob, json={"document_ids": ready, "quote_id": "0" * 64})
    assert (response.status_code, response.json()) == (404, {"detail": estimate_router.NOT_FOUND})
    assert runs(factory) == 0


# --- the start --------------------------------------------------------------------------------


def test_a_start_without_consent(
    factory: sessionmaker[Session], api: Api, alice: str, jobs_queue: RecordingQueue
) -> None:
    credential(factory, ALICE)
    ids = [document(api, alice, 0)]
    response = start(api, alice, ids)
    assert response.status_code == 409
    assert response.json() == {
        "detail": runs_router.CONSENT_REQUIRED,
        "code": "consent_required",
        "consent_version": estimate_router.CONSENT_VERSION,
    }
    assert runs(factory) == 0
    assert jobs_queue.jobs == []
    consent(api, alice)
    assert start(api, alice, ids).status_code == 201


def test_a_start_creates_the_run_and_queues_its_first_step(
    factory: sessionmaker[Session],
    api: Api,
    alice: str,
    ready: list[str],
    jobs_queue: RecordingQueue,
) -> None:
    response = start(api, alice, ready)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    run = response.json()
    assert (run["status"], run["partial"], run["waiting"]) == ("running", False, None)
    assert run["document_ids"] == ready
    assert [step["status"] for step in run["steps"]] == ["running", "planned"]
    assert [step["document_name"] for step in run["steps"]] == ["notes-0.md", "notes-1.md"]
    tokens = run["tokens"]
    assert tokens["ceiling"] == 2 * tokens["high"] > 0
    assert (tokens["spent"], tokens["history"]) == (0, [])
    assert tokens["overshoot_requests"] == engine.OVERSHOOT_REQUESTS
    assert (tokens["continue_adds"], tokens["continue_ceiling"], run["cost_usd"]) == (
        None,
        None,
        None,
    )
    counts = run["counts"]
    assert (counts["steps"], counts["running"], counts["planned"]) == (2, 1, 1)
    assert run["current_agent_run_id"] == run["steps"][0]["agent_run_id"]
    # The job holds the id of the agent run and nothing else.
    (job,) = [one for one in jobs_queue.jobs if one["function"] == "run_agent"]
    assert job["args"] == (run["current_agent_run_id"],)
    assert job["queue"] == queues.AGENTS
    # Neither the answer nor the agent run's input holds anything of the documents.
    assert MARKER not in response.text
    with factory() as db:
        agent = db.get_one(AgentRun, uuid.UUID(run["current_agent_run_id"]))
        assert MARKER not in json.dumps(agent.input)
    assert api.get(f"{RUNS}/{run['id']}", token=alice).json() == run
    listed = api.get(RUNS, token=alice).json()
    assert listed["active_run_id"] == run["id"]
    assert [one["id"] for one in listed["runs"]] == [run["id"]]


@pytest.mark.parametrize(
    "echo",
    [None, "", "0" * 63, "g" * 64, "A" * 64, "0" * 65, "\ud800" * 64, 7],
    ids=["missing", "empty", "short", "not_hex", "upper", "long", "surrogate", "number"],
)
def test_a_start_without_a_quote_id_or_with_a_malformed_one_is_422_and_never_a_run(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str], echo: Any
) -> None:
    body: dict[str, Any] = {"document_ids": ready}
    if echo is not None:
        body["quote_id"] = echo
    response = api.post(
        RUNS,
        token=alice,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert runs(factory) == 0


def changed(response: httpx.Response) -> str:
    """The body of a 409 `estimate_changed`: it carries the estimate of now and its new id."""
    assert response.status_code == 409, response.text
    body = response.json()
    assert (body["code"], body["detail"]) == ("estimate_changed", estimate_router.ESTIMATE_CHANGED)
    assert body["estimate"]["tokens"]["ceiling"] >= 0
    return str(body["estimate"]["quote_id"])


def test_a_stale_quote_id_because_the_credential_was_replaced_by_one_of_the_other_kind(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    shown = quote_id(api, alice, ready)
    credential(factory, ALICE, CredentialKind.claude_subscription)
    fresh = changed(start(api, alice, ready, quote_id=shown))
    assert fresh != shown
    assert runs(factory) == 0
    assert start(api, alice, ready, quote_id=fresh).status_code == 201


def test_a_stale_quote_id_because_the_selection_or_the_documents_to_read_again_differ(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    shown = quote_id(api, alice, ready)
    assert changed(start(api, alice, ready[:1], quote_id=shown)) != shown
    again = changed(start(api, alice, ready, quote_id=shown, reread_document_ids=ready[:1]))
    assert runs(factory) == 0
    response = start(api, alice, ready, quote_id=again, reread_document_ids=ready[:1])
    assert response.status_code == 201


def test_a_stale_quote_id_because_a_step_finished_in_between(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    shown = quote_id(api, alice, ready)
    run = started(api, alice, ready[:1])
    end_step(app, factory, run)  # the first document is read: the figures went down
    assert api.get(f"{RUNS}/{run['id']}", token=alice).json()["status"] == "done"
    fresh = changed(start(api, alice, ready, quote_id=shown))
    assert runs(factory) == 1
    response = start(api, alice, ready, quote_id=fresh)
    assert response.status_code == 201
    assert [step["status"] for step in response.json()["steps"]] == ["reused", "running"]


def test_a_new_consent_version_answers_consent_required_not_estimate_changed(
    factory: sessionmaker[Session],
    api: Api,
    alice: str,
    ready: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown = quote_id(api, alice, ready)
    monkeypatch.setattr(estimate_router, "CONSENT_VERSION", estimate_router.CONSENT_VERSION + 1)
    response = start(api, alice, ready, quote_id=shown)
    assert (response.status_code, response.json()["code"]) == (409, "consent_required")
    assert runs(factory) == 0


def test_a_start_without_a_credential(factory: sessionmaker[Session], api: Api, alice: str) -> None:
    consent(api, alice)
    response = start(api, alice, [document(api, alice, 0)])
    assert response.status_code == 409
    assert response.json() == {
        "detail": estimate_router.BLOCKERS["no_credential"],
        "code": "no_credential",
    }
    assert runs(factory) == 0


def test_a_start_naming_the_documents_that_cannot_be_read(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    set_columns(factory, ready[1], text_status=TextStatus.pending, text=None)
    response = start(api, alice, ready)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unreadable_documents"
    assert [(one["id"], one["name"], one["code"]) for one in body["documents"]] == [
        (ready[1], "notes-1.md", "pending")
    ]
    assert runs(factory) == 0


def test_a_start_with_a_selection_over_a_limit(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    over = [str(uuid.uuid4()) for _ in range(31)]
    response = api.post(RUNS, token=alice, json={"document_ids": over, "quote_id": "0" * 64})
    assert response.status_code == 422
    assert "30" in response.json()["detail"]
    none = api.post(RUNS, token=alice, json={"document_ids": [], "quote_id": "0" * 64})
    assert none.status_code == 422
    assert runs(factory) == 0


def test_a_second_start_while_a_run_is_active_answers_409_with_its_id(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready[:1])
    response = start(api, alice, ready[1:])
    assert response.status_code == 409
    assert response.json() == {
        "detail": runs_router.ACTIVE_RUN,
        "code": "active_run",
        "run_id": run["id"],
    }
    assert runs(factory) == 1


def test_redis_down_at_the_enqueue_of_the_first_step(
    factory: sessionmaker[Session],
    api: Api,
    alice: str,
    ready: list[str],
    jobs_queue: RecordingQueue,
) -> None:
    shown = quote_id(api, alice, ready)
    jobs_queue.down = True
    response = start(api, alice, ready, quote_id=shown)
    assert response.status_code == 201
    run = response.json()
    assert (run["status"], run["paused_reason"]) == ("paused", engine.QUEUE_DOWN)
    assert [step["status"] for step in run["steps"]] == ["planned", "planned"]
    assert run["steps"][0]["error"] == agent_runs.QUEUE_DOWN
    jobs_queue.down = False
    resumed = api.post(f"{RUNS}/{run['id']}/resume", token=alice)
    assert (resumed.status_code, resumed.json()["status"]) == (200, "running")
    assert resumed.json()["steps"][0]["status"] == "running"


# --- the run, read ----------------------------------------------------------------------------


def test_the_run_through_both_steps_to_done_then_a_rerun_reuses_them(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    end_step(app, factory, run, tokens=1_200)
    end_step(app, factory, run, tokens=800)
    done = api.get(f"{RUNS}/{run['id']}", token=alice).json()
    assert (done["status"], done["partial"], done["unread_document_ids"]) == ("done", False, [])
    assert done["tokens"]["spent"] == 2_000 < done["tokens"]["ceiling"]
    assert [step["tokens"] for step in done["steps"]] == [1_200, 800]
    assert (done["counts"]["done"], done["counts"]["proposals"]) == (2, 0)
    assert done["finished_at"] is not None
    assert done["current_agent_run_id"] is None

    # The estimate after a finished run shows the documents as reused.
    estimate = api.post(ESTIMATE, token=alice, json={"document_ids": ready}).json()
    assert (estimate["steps"], estimate["reused_steps"]) == (2, 2)
    assert estimate["tokens"] == {"low": 0, "high": 0, "ceiling": 0}
    again = started(api, alice, ready)
    assert [step["status"] for step in again["steps"]] == ["reused", "reused"]
    assert (again["status"], again["tokens"]["ceiling"], again["tokens"]["spent"]) == ("done", 0, 0)
    assert again["counts"]["reused"] == 2
    assert all(step["agent_run_id"] is None for step in again["steps"])


def test_the_cost_of_a_step_is_shown_only_when_it_ran_on_an_api_key(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    first = uuid.UUID(run["steps"][0]["agent_run_id"])
    eh.set_agent(
        factory, first, cost_usd="0.0312", credential_kind=CredentialKind.anthropic_api_key
    )
    end_step(app, factory, run, tokens=10)
    second = api.get(f"{RUNS}/{run['id']}", token=alice).json()["steps"][1]["agent_run_id"]
    eh.set_agent(
        factory,
        uuid.UUID(second),
        cost_usd="0.5",
        credential_kind=CredentialKind.claude_subscription,
    )
    steps = api.get(f"{RUNS}/{run['id']}", token=alice).json()["steps"]
    assert [step["cost_usd"] for step in steps] == ["0.0312", None]


def test_the_cost_of_a_run_counts_every_attempt_on_an_api_key_and_nothing_else(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    """A step keeps its last agent run only: the run's own amount adds the attempt a provider
    failure cut, leaves out what ran on a subscription and the agent runs of other runs."""
    run = started(api, alice, ready)
    url = f"{RUNS}/{run['id']}"
    key = CredentialKind.anthropic_api_key
    eh.set_agent(
        factory, uuid.UUID(run["current_agent_run_id"]), cost_usd="0.0312", credential_kind=key
    )
    assert api.get(url, token=alice).json()["cost_usd"] == "0.0312"
    end_step(app, factory, run, status=AgentStatus.failed, error=failures.REFUSED)
    resumed = api.post(f"{url}/resume", token=alice).json()
    second = uuid.UUID(resumed["current_agent_run_id"])
    eh.set_agent(factory, second, cost_usd="0.0100", credential_kind=key)
    end_step(app, factory, run, tokens=10)
    third = uuid.UUID(api.get(url, token=alice).json()["current_agent_run_id"])
    eh.set_agent(factory, third, cost_usd="0.5", credential_kind=CredentialKind.claude_subscription)
    end_step(app, factory, run, tokens=10)
    done = api.get(url, token=alice).json()
    assert done["status"] == "done"
    # The step shows its last attempt, the run every attempt on the key.
    assert [step["cost_usd"] for step in done["steps"]] == ["0.0100", None]
    assert done["cost_usd"] == "0.0412"
    # Another run of the same user is counted apart: here it reuses both steps and costs nothing.
    again = started(api, alice, ready)
    assert (again["status"], again["cost_usd"]) == ("done", None)
    assert api.get(url, token=alice).json()["cost_usd"] == "0.0412"


def test_the_cost_of_a_run_on_a_subscription_is_null(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    eh.set_agent(
        factory,
        uuid.UUID(run["current_agent_run_id"]),
        cost_usd="0.25",
        credential_kind=CredentialKind.claude_subscription,
    )
    end_step(app, factory, run, tokens=10)
    assert api.get(f"{RUNS}/{run['id']}", token=alice).json()["cost_usd"] is None


def test_the_overshoot_sentence_is_the_one_g6_measured() -> None:
    """The plan quotes this sentence (G11, and Measurements, G6) for two requests at most."""
    assert engine.OVERSHOOT_REQUESTS == 2
    assert runs_router.OVERSHOOT == "A run can pass its ceiling by one request, or two."


def test_the_tokens_of_the_running_step_are_counted_in_what_was_spent(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    eh.set_agent(
        factory, uuid.UUID(run["current_agent_run_id"]), input_tokens=900, output_tokens=100
    )
    assert api.get(f"{RUNS}/{run['id']}", token=alice).json()["tokens"]["spent"] == 1_000


# --- cancel, resume, continue -----------------------------------------------------------------


def test_cancel_while_queued_then_again(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    response = api.post(f"{RUNS}/{run['id']}/cancel", token=alice)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["cancel_requested"]) == ("cancelled", True)
    assert [step["status"] for step in body["steps"]] == ["cancelled", "cancelled"]
    again = api.post(f"{RUNS}/{run['id']}/cancel", token=alice)
    assert (again.status_code, again.json()) == (409, {"detail": runs_router.ENDED})
    for action in ("resume", "continue"):
        again = api.post(f"{RUNS}/{run['id']}/{action}", token=alice, json={"adds": 1})
        assert again.status_code == 409


def test_cancel_mid_step_is_honoured_when_the_step_has_ended(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    agent_id = uuid.UUID(run["current_agent_run_id"])
    eh.set_agent(factory, agent_id, status=AgentStatus.running)
    body = api.post(f"{RUNS}/{run['id']}/cancel", token=alice).json()
    assert (body["status"], body["cancel_requested"]) == ("running", True)
    with factory() as db:
        assert db.get_one(AgentRun, agent_id).cancel_requested_at is not None
    end_step(app, factory, run, status=AgentStatus.cancelled)
    assert api.get(f"{RUNS}/{run['id']}", token=alice).json()["status"] == "cancelled"


def test_resume_and_continue_on_a_running_run_are_refused(
    api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    resumed = api.post(f"{RUNS}/{run['id']}/resume", token=alice)
    assert (resumed.status_code, resumed.json()) == (409, {"detail": runs_router.NOT_RESUMABLE})
    continued = api.post(f"{RUNS}/{run['id']}/continue", token=alice, json={"adds": 1})
    assert (continued.status_code, continued.json()) == (
        409,
        {"detail": runs_router.NOT_CONTINUABLE},
    )


def test_a_provider_failure_pauses_and_resume_runs_the_failed_step(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    end_step(app, factory, run, status=AgentStatus.failed, error=failures.REFUSED)
    paused = api.get(f"{RUNS}/{run['id']}", token=alice).json()
    assert (paused["status"], paused["paused_reason"]) == ("paused", failures.REFUSED)
    assert paused["steps"][0]["error"] == failures.REFUSED
    # Continue is for the ceiling only.
    assert (
        api.post(f"{RUNS}/{run['id']}/continue", token=alice, json={"adds": 1}).status_code == 409
    )
    resumed = api.post(f"{RUNS}/{run['id']}/resume", token=alice).json()
    assert (resumed["status"], resumed["paused_reason"]) == ("running", None)
    assert resumed["steps"][0]["status"] == "running"
    assert resumed["steps"][0]["agent_run_id"] != paused["steps"][0]["agent_run_id"]


def test_continue_raises_the_ceiling_by_h_and_the_history_holds_one_entry(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    ceiling, high = run["tokens"]["ceiling"], run["tokens"]["high"]
    end_step(app, factory, run, tokens=ceiling)
    paused = api.get(f"{RUNS}/{run['id']}", token=alice).json()
    assert (paused["status"], paused["paused_reason"]) == ("paused", "token_ceiling")
    # What the click buys, said before the click: one `H` here, the next step was never cut.
    assert paused["tokens"]["continue_adds"] == high
    assert paused["tokens"]["continue_ceiling"] == ceiling + high
    assert run["tokens"]["continue_adds"] is None
    assert api.post(f"{RUNS}/{run['id']}/resume", token=alice).status_code == 409
    response = api.post(f"{RUNS}/{run['id']}/continue", token=alice, json={"adds": high})
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["tokens"]["ceiling"]) == ("running", ceiling + high)
    # The ceiling the page showed before the click is the one the click set.
    assert body["tokens"]["ceiling"] == paused["tokens"]["continue_ceiling"]
    assert (body["tokens"]["continue_adds"], body["tokens"]["continue_ceiling"]) == (None, None)
    (entry,) = body["tokens"]["history"]
    assert (entry["spent"], entry["from"], entry["to"]) == (ceiling, ceiling, ceiling + high)
    assert body["steps"][1]["status"] == "running"
    assert (
        api.post(f"{RUNS}/{run['id']}/continue", token=alice, json={"adds": 1}).status_code == 409
    )


def test_a_run_paused_with_nothing_left_to_read_asks_no_raise_and_a_continue_closes_it(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    """Issue #158: the last planned document is deleted during a ceiling pause, through the
    route and its guard. The run shows that one Continue adds nothing, and the click of a page
    that still shows one `H` adds nothing and is refused (409, figure 0): the run closes on the
    ceiling it had."""
    run = started(api, alice, ready)
    url = f"{RUNS}/{run['id']}"
    ceiling, high = run["tokens"]["ceiling"], run["tokens"]["high"]
    end_step(app, factory, run, tokens=ceiling)
    assert api.get(url, token=alice).json()["tokens"]["continue_adds"] == high
    assert delete(api, alice, ready[1]).status_code == 204

    paused = api.get(url, token=alice).json()
    assert (paused["status"], paused["paused_reason"]) == ("paused", "token_ceiling")
    assert paused["tokens"]["continue_adds"] == 0
    assert paused["tokens"]["continue_ceiling"] is None  # no raise is asked, no ceiling shown
    before = agents_started(factory)
    response = api.post(f"{url}/continue", token=alice, json={"adds": high})
    assert response.status_code == 409  # a click never looks accepted when it was not
    assert response.json()["code"] == "continue_changed"
    assert response.json()["continue_adds"] == 0
    body = response.json()["run"]
    assert (body["status"], body["partial"]) == ("done", True)  # and the run is closed
    assert (body["tokens"]["ceiling"], body["tokens"]["history"]) == (ceiling, [])
    assert (body["tokens"]["spent"], body["tokens"]["continue_adds"]) == (ceiling, None)
    assert agents_started(factory) == before


def agents_started(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) or 0)


def test_a_continue_from_a_stale_page_is_refused_and_adds_nothing(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    """B1 of the second review of PR #148, the reviewer's two tab probe: both tabs show what one
    Continue adds at the first pause, the first tab clicks, the step is cut again and the figure
    grows. The second tab's click carries the old figure: it is refused, nothing is added, no
    agent run starts, and the answer holds the figure of now."""
    run = started(api, alice, ready)
    url = f"{RUNS}/{run['id']}"
    ceiling, high = run["tokens"]["ceiling"], run["tokens"]["high"]
    end_step(app, factory, run, tokens=ceiling)
    first = api.get(url, token=alice).json()["tokens"]["continue_adds"]
    assert first == high  # what both tabs show

    tab_a = api.post(f"{url}/continue", token=alice, json={"adds": first})
    assert tab_a.status_code == 200
    budget = eh.running(factory, uuid.UUID(run["id"])).token_budget
    end_step(
        app, factory, run, status=AgentStatus.failed, error=failures.TOKEN_LIMIT, tokens=budget
    )
    paused = api.get(url, token=alice).json()
    now = paused["tokens"]["continue_adds"]
    assert paused["paused_reason"] == "token_ceiling"
    assert now == 2 * budget > first  # the step that was cut starts again on twice its budget
    assert paused["tokens"]["continue_ceiling"] == paused["tokens"]["ceiling"] + now

    before = agents_started(factory)
    for stale in (first, now - 1, now + 1):  # the other tab, then a figure under and one over
        tab_b = api.post(f"{url}/continue", token=alice, json={"adds": stale})
        assert tab_b.status_code == 409
        body = tab_b.json()
        assert (body["detail"], body["code"]) == (runs_router.CONTINUE_CHANGED, "continue_changed")
        assert body["continue_adds"] == now
        assert body["run"] == paused  # the run as it is: nothing was written
        assert api.get(url, token=alice).json() == paused
        assert agents_started(factory) == before
    assert " - " not in runs_router.CONTINUE_CHANGED

    agreed = api.post(f"{url}/continue", token=alice, json={"adds": now})
    assert agreed.status_code == 200
    body = agreed.json()
    assert body["tokens"]["ceiling"] == paused["tokens"]["ceiling"] + now  # exactly that figure
    assert body["tokens"]["ceiling"] == paused["tokens"]["continue_ceiling"]
    assert [(one["from"], one["to"]) for one in body["tokens"]["history"]] == [
        (ceiling, ceiling + first),
        (ceiling + first, ceiling + first + now),
    ]
    assert agents_started(factory) == before + 1
    # The same click sent again (a client that retries after a timeout): the run is running.
    again = api.post(f"{url}/continue", token=alice, json={"adds": now})
    assert (again.status_code, again.json()) == (409, {"detail": runs_router.NOT_CONTINUABLE})


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"adds": None},
        {"adds": "88110"},
        {"adds": 88110.5},
        {"adds": 88110.0},
        {"adds": True},
        {"adds": 0},
        {"adds": -1},
        {"adds": runs_router.ADDS_MAX + 1},
        {"adds": 10**400},
        {"adds": [88110]},
        [88110],
    ],
)
def test_a_continue_without_its_figure_or_with_a_malformed_one_is_422_and_adds_nothing(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str], body: Any
) -> None:
    run = started(api, alice, ready)
    url = f"{RUNS}/{run['id']}"
    end_step(app, factory, run, tokens=run["tokens"]["ceiling"])
    paused = api.get(url, token=alice).json()
    response = (
        api.post(f"{url}/continue", token=alice)
        if body is None
        else api.post(f"{url}/continue", token=alice, json=body)
    )
    assert response.status_code == 422
    assert api.get(url, token=alice).json() == paused


# --- deleting a document ----------------------------------------------------------------------


def test_deleting_a_document_read_by_a_step_and_one_no_step_has_read(
    factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    other = document(api, alice, 7)
    started(api, alice, ready)
    # Since G15 a document a run reads is deleted too, with what was drawn from it.
    assert delete(api, alice, ready[0]).status_code == 204
    assert delete(api, alice, other).status_code == 204
    assert sorted(str(one.id) for one in rows(factory)) == sorted(ready[1:])
