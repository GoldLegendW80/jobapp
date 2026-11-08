"""Assess this posting (app/jobs/assess.py, assess_tools.py, assess_router.py), against real
Postgres with the queue recorded instead of Redis: the agent in the catalog, internal and bound
to its job; the submit's checks (shape, the inferred gate, risks and mitigations, quotes word
for word), its sort, its dash rule, once per run, the last assessment replaced; the keyword fit
never touched; the click that starts one run and a second click that starts nothing; the view;
every refusal and another person's posting."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import catalog, definitions, runs
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, RunStatus
from app.jobs import assess
from app.jobs import service as jobs_service
from app.jobs.assess_models import JobAssessment
from app.jobs.models import Job
from tests.agents.conftest import API_KEY, FakeAnthropic, build_app, client_for, new_key
from tests.agents.run_helpers import (
    add_user,
    context,
    events,
    execute,
    new_run,
    save_credential,
)
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock, make_user

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)

POSTING = """Example Robotics is hiring a Senior data engineer in Lyon.
Requirements: five years of Python is a must have. Fluent French is required.
Nice to have: Kafka.
You will report to the Head of Data, within the Data Platform team.
You will own the ingestion pipelines and you will lead the migration to dbt.
You will work closely with product and finance.
This is a 12 month fixed term contract."""


def requirement(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "requirement": "Five years of Python",
        "importance": "critical",
        "tier": "stated",
        "signal": "five years of Python is a must have",
        "match": "strong",
        "profile_evidence": "Six years of Python at Example Freight.",
    }
    return {**row, **over}


def payload(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "requirements": [
            requirement(),
            requirement(
                requirement="Kafka",
                importance="preferred",
                tier="structural",
                signal="Under Nice to have",
                match="missing",
                profile_evidence="Not in your profile.",
            ),
            requirement(
                requirement="Fluent French",
                importance="critical",
                tier="stated",
                signal="\u201cFluent  French is required\u201d",
                match="missing",
                profile_evidence="Your profile lists English only.",
                interview_risk="A French speaking panel may test it in the first call.",
                mitigation="Say you follow French courses and where you stand today.",
            ),
            requirement(
                requirement="Stakeholder communication",
                importance="meaningful",
                tier="inferred",
                signal=None,
                match="partial",
                profile_evidence="You presented to finance once a quarter.",
            ),
        ],
        "more_requirements": 2,
        "culture": [
            {
                "criterion": "Hybrid work",
                "finding": "not_stated",
                "evidence": "The posting does not say where the work happens.",
            }
        ],
        "signals": [
            {
                "kind": "fixed_term",
                "quote": "This is a 12 month fixed term contract.",
                "note": "A fixed term of a year.",
            }
        ],
        "verdict": "A strong match on Python \u2014 French is the gap to prepare.",
        "confidence": "medium",
    }
    return {**body, **over}


def add_job(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    fit: int | None = 72,
    company: str = "Example Robotics",
) -> uuid.UUID:
    with factory() as db, db.begin():
        job = jobs_service.create_job(
            db,
            user_id,
            jobs_service.NewJob(
                company=company, title="Senior data engineer", posting_text=POSTING
            ),
            NOW,
        )
        job.fit = fit
        return job.id


def add_run(factory: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID) -> uuid.UUID:
    with factory() as db, db.begin():
        return runs.create_run(db, user_id, assess.NAME, job_id, {}, "claude-opus-5-5", NOW).id


def stored_row(factory: sessionmaker[Session], job_id: uuid.UUID) -> JobAssessment | None:
    with factory() as db:
        return db.get(JobAssessment, job_id)


@pytest.fixture
def person(factory: sessionmaker[Session]) -> uuid.UUID:
    return make_user(factory, email="e2e-ada@example.org", name="Ada", password_hash="h").id


@pytest.fixture
def other(factory: sessionmaker[Session]) -> uuid.UUID:
    return make_user(factory, email="e2e-bo@example.org", name="Bo", password_hash="h").id


# --- the agent -----------------------------------------------------------------------------------


def test_the_agent_is_internal_bound_to_its_job_and_listed_in_the_catalog() -> None:
    agent = catalog.load()[assess.NAME]
    assert agent.internal
    assert agent.needs_job
    assert agent.bind_job
    assert agent.tools == ("get_job", "get_profile_section", assess.SUBMIT)
    assert not agent.private_io
    assert agent.model_for(CredentialKind.claude_subscription) == "claude-opus-5-5"
    assert agent.model_for(CredentialKind.anthropic_api_key) == "claude-sonnet-5-5"
    assert catalog.category_of(agent) == "jobs"
    assert catalog.starter(agent) == "The Assess button of a posting's page"
    assert definitions.module_of(assess.NAME) == "app.jobs.assess"
    # The worker knows the tool, under the name the definition gives it.
    assert assess.SUBMIT in app_tools.FACTORIES
    assert agent.prompt({}, uuid.UUID(int=7)).endswith(f"Job id: {uuid.UUID(int=7)}")


# --- the shape -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"requirements": [requirement(tier="inferred", signal=None)]}, "requirements.0"),
        (
            {"requirements": [requirement(tier="inferred", importance="high", signal=None)]},
            "requirements.0",
        ),
        ({"requirements": [requirement(signal=None)]}, "requirements.0"),
        ({"requirements": [requirement(tier="structural", signal=None)]}, "requirements.0"),
        ({"requirements": [requirement(match="missing")]}, "requirements.0"),
        (
            {"requirements": [requirement(importance="high", match="partial", mitigation="x")]},
            "requirements.0",
        ),
        ({"requirements": [requirement()] * 13}, "requirements"),
        ({"requirements": [requirement(importance="essential")]}, "requirements.0.importance"),
        ({"requirements": [{**requirement(), "score": 5}]}, "requirements.0.score"),
        ({"confidence": "certain"}, "confidence"),
        ({"verdict": ""}, "verdict"),
        ({"verdict": "x" * 401}, "verdict"),
        ({"more_requirements": -1}, "more_requirements"),
        ({"signals": [{"kind": "layoffs", "quote": "x", "note": "y"}]}, "signals.0.kind"),
        ({"fit": 90}, "fit"),
    ],
)
def test_a_submit_that_breaks_a_rule_answers_where(change: dict[str, Any], where: str) -> None:
    with pytest.raises(ValidationError) as caught:
        assess.Submitted.model_validate(payload(**change))
    first = caught.value.errors()[0]
    assert ".".join(str(part) for part in first["loc"]).startswith(where)


def test_a_complete_submit_passes_and_an_inferred_meaningful_row_needs_no_signal() -> None:
    parsed = assess.Submitted.model_validate(payload())
    assert len(parsed.requirements) == 4
    meaningful = assess.Submitted.model_validate(
        payload(requirements=[requirement(tier="inferred", importance="meaningful", signal=None)])
    )
    assert meaningful.requirements[0].signal is None


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("five years of Python is a must have", True),
        ("FIVE  years of python\nis a must have", True),
        ("\u201cFluent French is required\u201d", True),
        ("Requirements: five years ... Fluent French is required.", True),
        ("Requirements: five years \u2026 Kafka", True),
        ("Nice to have: Kafka.", True),
        ("Kafka.", True),
        ("Kafka ... Requirements", False),
        ("six years of Python is a must have", False),
        ("...", False),
        ("", False),
    ],
)
def test_a_quote_counts_when_its_words_are_in_the_posting_in_order(
    quote: str, expected: bool
) -> None:
    assert assess.in_posting(quote, POSTING) is expected


# --- the submit ----------------------------------------------------------------------------------


def test_the_submit_stores_sorted_rows_with_role_clarity_and_never_touches_the_fit(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, person, job_id)
    text, error = assess.submit(factory, person, run_id, job_id, payload(), NOW)
    assert (text, error) == (assess.STORED, False)
    row = stored_row(factory, job_id)
    assert row is not None
    assert (row.user_id, row.run_id, row.created_at) == (person, run_id, NOW)
    result = row.result
    # Importance first, then unmet before met within a band.
    assert [(r["requirement"], r["importance"], r["match"]) for r in result["requirements"]] == [
        ("Fluent French", "critical", "missing"),
        ("Five years of Python", "critical", "strong"),
        ("Stakeholder communication", "meaningful", "partial"),
        ("Kafka", "preferred", "missing"),
    ]
    # The posting's words are kept as quoted; what the model wrote follows the dash rule.
    assert result["requirements"][0]["signal"] == "\u201cFluent  French is required\u201d"
    assert "\u2014" not in result["verdict"]
    assert result["verdict"] == "A strong match on Python, French is the gap to prepare."
    assert result["signals"][0]["quote"] == "This is a 12 month fixed term contract."
    assert result["more_requirements"] == 2
    assert result["confidence"] == "medium"
    clarity = result["role_clarity"]
    assert clarity["read"] is True
    statuses = {point["id"]: point["status"] for point in clarity["points"]}
    assert statuses["supervisor"] == "present"
    assert statuses["coordination"] == "present"
    with factory() as db:
        assert db.get(Job, job_id).fit == 72


def test_a_second_submit_of_the_same_run_changes_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, person, job_id)
    assess.submit(factory, person, run_id, job_id, payload(), NOW)
    later = NOW + timedelta(minutes=1)
    again = assess.submit(factory, person, run_id, job_id, payload(confidence="high"), later)
    assert again == (assess.SUBMITTED_ONCE, True)
    row = stored_row(factory, job_id)
    assert row is not None
    assert (row.created_at, row.result["confidence"]) == (NOW, "medium")


def test_a_new_run_replaces_the_last_assessment(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    first = add_run(factory, person, job_id)
    assess.submit(factory, person, first, job_id, payload(), NOW)
    second = add_run(factory, person, job_id)
    later = NOW + timedelta(days=1)
    assert assess.submit(factory, person, second, job_id, payload(confidence="low"), later) == (
        assess.STORED,
        False,
    )
    row = stored_row(factory, job_id)
    assert row is not None
    assert (row.run_id, row.created_at, row.result["confidence"]) == (second, later, "low")
    with factory() as db:
        count = db.scalar(sa.select(sa.func.count()).select_from(JobAssessment))
    assert count == 1


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (
            {"requirements": [requirement(signal="ten years of Rust is a must have")]},
            "requirements.0.signal",
        ),
        (
            {"signals": [{"kind": "contradiction", "quote": "Junior role", "note": "Junior."}]},
            "signals.0.quote",
        ),
    ],
)
def test_a_quote_the_posting_does_not_hold_stores_nothing(
    factory: sessionmaker[Session], person: uuid.UUID, change: dict[str, Any], where: str
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, person, job_id)
    text, error = assess.submit(factory, person, run_id, job_id, payload(**change), NOW)
    assert error
    assert text.startswith(f"Not stored: {where}: this quote is not in the posting.")
    assert stored_row(factory, job_id) is None


def test_a_malformed_submit_answers_what_to_fix_and_stores_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, person, job_id)
    bad = payload(requirements=[requirement(tier="inferred", signal=None)])
    text, error = assess.submit(factory, person, run_id, job_id, bad, NOW)
    assert error
    assert text.startswith("Not stored: requirements.0: ")
    assert "never critical or high" in text
    assert text.endswith("Fix it and submit again.")
    assert stored_row(factory, job_id) is None


def test_another_persons_posting_and_a_run_without_job_store_nothing(
    factory: sessionmaker[Session], person: uuid.UUID, other: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, other, job_id)
    assert assess.submit(factory, other, run_id, job_id, payload(), NOW) == (assess.JOB_GONE, True)
    assert assess.submit(factory, person, None, job_id, payload(), NOW) == (assess.NO_RUN, True)
    assert assess.submit(factory, person, run_id, None, payload(), NOW) == (assess.NO_RUN, True)
    assert stored_row(factory, job_id) is None


def test_the_tool_stores_for_its_own_run_and_job(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add_job(factory, person)
    run_id = add_run(factory, person, job_id)
    scope = app_tools.Scope(
        sessions=factory, user_id=person, run_id=run_id, job_id=job_id, clock=lambda: NOW
    )
    made = app_tools.FACTORIES[assess.SUBMIT](scope)
    assert made.name == assess.SUBMIT
    answer = asyncio.run(made.handler(payload()))
    assert answer["content"][0]["text"] == assess.STORED
    assert "is_error" not in answer
    row = stored_row(factory, job_id)
    assert row is not None
    assert row.run_id == run_id


def test_the_submit_reads_the_description_when_the_posting_has_no_text(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    with factory() as db, db.begin():
        job = jobs_service.create_job(
            db,
            person,
            jobs_service.NewJob(company="Example Labs", title="Analyst", description=POSTING),
            NOW,
        )
        job_id = job.id
    run_id = add_run(factory, person, job_id)
    assert assess.submit(factory, person, run_id, job_id, payload(), NOW) == (assess.STORED, False)


def test_a_run_on_the_fake_backend_stores_its_assessment(
    factory: sessionmaker[Session], migrated_url: str, tmp_path: Path
) -> None:
    key = new_key()
    settings = make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(tmp_path))
    keyring = Keyring.from_text(key)
    user = add_user(factory, "e2e-run@example.org")
    save_credential(factory, keyring, user)
    job_id = add_job(factory, user)
    run_id = new_run(factory, user, {}, job_id=job_id, agent=assess.NAME)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    row = stored_row(factory, job_id)
    assert row is not None
    assert row.run_id == run_id
    assert row.result["confidence"] == "low"
    assert row.result["role_clarity"]["read"] is True
    called = [data.get("name") for kind, data in events(factory, run_id) if kind == "tool_call"]
    assert called == [
        "mcp__app__get_job",
        "mcp__app__get_profile_section",
        "mcp__app__submit_assessment",
    ]
    with factory() as db:
        assert db.get(Job, job_id).fit == 72


# --- the routes ----------------------------------------------------------------------------------


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], str, str]] = []
        self.down = False

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if self.down:
            raise queues.QueueUnavailableError("down")
        self.jobs.append((function, args, queue, job_id))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def app(migrated_url: str, clock: Clock) -> FastAPI:
    return build_app(migrated_url, clock, FakeAnthropic(), new_key())


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    yield from client_for(app)


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def sign_in(factory: sessionmaker[Session], api: Api, email: str) -> tuple[str, uuid.UUID]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    return api.login_token(email), user.id


def save_key(api: Api, token: str) -> None:
    response = api.request(
        "PUT",
        "/api/me/model-credential",
        token=token,
        json={"kind": "anthropic_api_key", "secret": API_KEY},
    )
    assert response.status_code == 200, response.text


def test_the_click_queues_one_run_and_a_second_click_answers_it(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    token, user_id = sign_in(factory, api, "e2e-ada@example.org")
    save_key(api, token)
    job_id = add_job(factory, user_id)
    response = api.post(f"/api/jobs/{job_id}/assess", token=token)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    run = response.json()
    assert (run["agent"], run["job_id"], run["status"]) == (assess.NAME, str(job_id), "queued")
    assert run["job"] == {
        "id": str(job_id),
        "company": "Example Robotics",
        "title": "Senior data engineer",
    }
    assert recorded.jobs == [
        ("run_agent", (run["id"],), queues.AGENTS, queues.agent_run_job_id(run["id"]))
    ]
    again = api.post(f"/api/jobs/{job_id}/assess", token=token)
    assert again.status_code == 202
    assert again.json()["id"] == run["id"]
    assert len(recorded.jobs) == 1
    # Once it ended, the next click starts a new run.
    with factory() as db, db.begin():
        runs.fail_queued(db, uuid.UUID(run["id"]), "Stopped.", NOW)
    third = api.post(f"/api/jobs/{job_id}/assess", token=token)
    assert third.status_code == 202
    assert third.json()["id"] != run["id"]
    assert len(recorded.jobs) == 2


def test_the_click_refusals(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    token, user_id = sign_in(factory, api, "e2e-ada@example.org")
    other_token, other_id = sign_in(factory, api, "e2e-bo@example.org")
    job_id = add_job(factory, user_id)
    no_key = api.post(f"/api/jobs/{job_id}/assess", token=token)
    assert no_key.status_code == 409
    assert "credential" in no_key.json()["detail"]
    save_key(api, other_token)
    assert api.post(f"/api/jobs/{job_id}/assess", token=other_token).status_code == 404
    assert api.post("/api/jobs/not-a-uuid/assess", token=other_token).status_code == 404
    assert api.post(f"/api/jobs/{uuid.uuid4()}/assess", token=other_token).status_code == 404
    # The agents route never starts it, nor lists it: only its own button does.
    started = api.post(
        "/api/agents/runs",
        token=other_token,
        json={"agent": assess.NAME, "job_id": str(add_job(factory, other_id)), "input": {}},
    )
    assert started.status_code == 422
    listed = api.get("/api/agents", token=other_token).json()["items"]
    assert assess.NAME not in [item["name"] for item in listed]
    recorded.down = True
    own = add_job(factory, other_id, company="Example Labs")
    down = api.post(f"/api/jobs/{own}/assess", token=other_token)
    assert down.status_code == 503
    assert recorded.jobs == []
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(JobAssessment)) == 0


def test_a_click_past_the_runs_at_once_is_429_and_starts_nothing(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    token, user_id = sign_in(factory, api, "e2e-ada@example.org")
    save_key(api, token)
    job_id = add_job(factory, user_id)
    busy = add_job(factory, user_id, company="Example Labs")
    with factory() as db, db.begin():
        for _ in range(runs.ACTIVE_PER_USER):
            runs.create_run(db, user_id, "triage", busy, {}, "claude-opus-5-5", NOW)
    response = api.post(f"/api/jobs/{job_id}/assess", token=token)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert recorded.jobs == []
    assert api.get(f"/api/jobs/{job_id}/assessment", token=token).json()["run"] is None


def test_the_view_shows_the_latest_run_and_the_last_assessment(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    token, user_id = sign_in(factory, api, "e2e-ada@example.org")
    other_token, _ = sign_in(factory, api, "e2e-bo@example.org")
    save_key(api, token)
    job_id = add_job(factory, user_id)
    path = f"/api/jobs/{job_id}/assessment"
    empty = api.get(path, token=token)
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json() == {"run": None, "assessment": None}
    run_id = api.post(f"/api/jobs/{job_id}/assess", token=token).json()["id"]
    queued = api.get(path, token=token).json()
    assert queued == {"run": {"id": run_id, "status": "queued", "error": None}, "assessment": None}
    assess.submit(factory, user_id, uuid.UUID(run_id), job_id, payload(), NOW)
    shown = api.get(path, token=token).json()
    assert shown["assessment"]["run_id"] == run_id
    assert shown["assessment"]["result"]["verdict"].startswith("A strong match on Python")
    assert shown["assessment"]["result"]["role_clarity"]["read"] is True
    assert api.get(path, token=other_token).status_code == 404
    assert api.get("/api/jobs/not-a-uuid/assessment", token=token).status_code == 404


def test_both_routes_need_a_session_and_the_click_needs_csrf(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    token, user_id = sign_in(factory, api, "e2e-ada@example.org")
    job_id = add_job(factory, user_id)
    assert api.request("GET", f"/api/jobs/{job_id}/assessment").status_code == 401
    assert api.request("POST", f"/api/jobs/{job_id}/assess").status_code == 401
    save_key(api, token)
    no_csrf = api.request("POST", f"/api/jobs/{job_id}/assess", token=token, csrf=False)
    assert no_csrf.status_code == 403
    assert recorded.jobs == []
