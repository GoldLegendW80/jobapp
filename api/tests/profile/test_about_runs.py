"""The "About you" document with real Postgres (app/profile/about.py, about_agent.py,
about_router.py): the conversion once (skills, facts and answers only), the autosave, Done on the
fake backend (the recorded stream `profile_interpret`, a Skills reading: no model is called, the
key is a made up value of the right shape), what the app understood, Apply (one revision per
section, the rescore after a skills change), Undo, a fact part applied by code, a consent changed
only on Apply, the states without a key and the cost line."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.jobs.models import JobRescore
from app.profile import about, about_agent, about_router, about_text
from app.profile import service as profile_service
from app.profile.models import Profile, ProfileAbout, ProfileRevision
from tests.identity.api_helpers import Api, make_user
from tests.profile.test_titles_runs import RecordingQueue, Runtime, give_key, save

EMAIL = "e2e-about@example.org"
OTHER = "e2e-about-other@example.org"
URL = "/api/profile/about"
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
EXPERIENCE = {
    "employers": [
        {
            "key": "example-robotics",
            "organisation": "Example Robotics",
            "title": "Data engineer",
            "start": "2021-03",
            "end": None,
            "location": "Lyon",
            "contract": "CDI",
        }
    ],
    "education": [],
}
SKILLS = {
    "years_experience": 4,
    "have": [{"name": "data", "terms": ["SQL", "dbt"], "advanced_regex": False}],
}
ANSWERS = {"notice_period": "Three months.", "background_check_consent": False}
# What the person types in the Skills part before Done (the stream reads this).
EDITED = (
    "Years of experience: 5\n\n### What I have\n- data: SQL, dbt, Airflow\n\n"
    "### What I do not have\n- Java"
)
LINES = [
    "Years of experience: 5",
    "1 skill added to what I have: Airflow",
    "1 skill added to what I do not have: Java",
]


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def person(factory: sessionmaker[Session], email: str = EMAIL) -> uuid.UUID:
    user_id = make_user(factory, email=email, name=email.split("@")[0]).id
    save(factory, user_id, "experience", EXPERIENCE)
    save(factory, user_id, "skills", SKILLS)
    save(factory, user_id, "answers", ANSWERS)
    return user_id


def signed(factory: sessionmaker[Session], api: Api, email: str = EMAIL) -> tuple[uuid.UUID, str]:
    return person(factory, email), api.login_token(email)


def put(api: Api, token: str, version: int, text: str) -> Any:
    return api.request("PUT", URL, token=token, json={"version": version, "text": text})


def with_part(text: str, kind: str, body: str) -> str:
    preamble, parts = about_text.split(text)
    parts = [about_text.Part(p.heading, body) if p.kind == kind else p for p in parts]
    return about_text.join(preamble, parts)


def revisions(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[str]:
    with factory() as db:
        rows = db.scalars(
            sa.select(ProfileRevision.section)
            .where(ProfileRevision.user_id == user_id)
            .order_by(ProfileRevision.revision)
        )
        return list(rows)


def sections(factory: sessionmaker[Session], user_id: uuid.UUID) -> dict[str, Any]:
    with factory() as db:
        profile = db.get(Profile, user_id)
        assert profile is not None
        return dict(profile.sections)


def test_the_first_read_writes_skills_facts_and_answers_once_and_changes_no_section(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    before = revisions(factory, user_id)
    first = api.get(URL, token=token)
    assert first.status_code == 200, first.text
    body = first.json()
    assert first.headers["cache-control"] == "no-store"
    assert body["text"].startswith("## Skills\n\nYears of experience: 4\n\n### What I have\n")
    assert "### Background checks\nNo, I do not consent to background checks." in body["text"]
    # The jobs stay a form: the document does not hold them (owner, 2026-11-05).
    assert "Example Robotics" not in body["text"]
    assert body["version"] == 0
    assert [(p["heading"], p["kind"], p["state"]) for p in body["parts"]] == [
        ("Skills", "skills", "in_step"),
        ("Answers", "answers", "in_step"),
    ]
    assert body["needs"] == "model_key"
    assert all(p["estimate"] is None for p in body["parts"])
    assert revisions(factory, user_id) == before
    # A profile saved since does not rewrite the document: the part says so instead.
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 6}, T0)
    again = api.get(URL, token=token).json()
    assert again["text"] == body["text"]
    assert again["parts"][0]["state"] == "elsewhere"
    # Another person reads their own document only.
    make_user(factory, email=OTHER, name="other")
    other = api.login_token(OTHER)
    assert "Three months." not in api.get(URL, token=other).json()["text"]


def test_the_text_saves_as_typed_and_a_stale_version_gets_the_current_one(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    _, token = signed(factory, api)
    text = api.get(URL, token=token).json()["text"]
    changed = with_part(text, "skills", EDITED)
    saved = put(api, token, 0, changed)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["parts"][0]["state"] == "edited"
    stale = put(api, token, 0, text)
    assert stale.status_code == 409
    assert stale.json()["detail"] == about_router.STALE
    assert stale.json()["current"]["text"] == changed
    assert put(api, token, 1, "x" * (about_text.TEXT_MAX + 1)).status_code == 422
    assert put(api, token, 1, "## Skills\n\u0007").status_code == 422


def test_without_a_key_done_is_refused_and_the_page_says_so(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    _, token = signed(factory, api)
    body = api.get(URL, token=token).json()
    refused = api.post(f"{URL}/done", token=token, json={"heading": "Skills", "version": 0})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Add a model key on the Account page first."
    assert recorded.jobs == []
    assert body["needs"] == "model_key"


def test_the_cost_shows_on_an_api_key_and_never_on_a_subscription(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    estimate = api.get(URL, token=token).json()["parts"][0]["estimate"]
    assert estimate["tokens"]["low"] < estimate["tokens"]["high"]
    assert estimate["cost"]["currency"] == "USD"
    assert 0 < float(estimate["cost"]["low"]) < float(estimate["cost"]["high"]) < 0.5
    other_id = make_user(factory, email=OTHER, name="other").id
    give_key(factory, other_id, CredentialKind.claude_subscription)
    estimate = api.get(URL, token=api.login_token(OTHER)).json()["parts"][0]["estimate"]
    assert estimate["cost"] is None
    assert estimate["tokens"]["high"] > 0


def test_done_starts_one_reading_and_an_empty_part_needs_no_model(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    api.get(URL, token=token)
    started = api.post(f"{URL}/done", token=token, json={"heading": "skills", "version": 0})
    assert started.status_code == 200, started.text
    assert started.json()["reading"]["status"] == "running"
    [(function, (run_id,), _, job)] = recorded.jobs
    assert function == about_agent.JOB
    assert job == about_agent.job_id(run_id)
    with factory() as db:
        run = db.get(AgentRun, uuid.UUID(run_id))
        assert run is not None
        assert run.agent == about.NAME
        # The input holds a random key and the budget, never the text.
        assert set(run.input) == {"request", "token_budget"}
    again = api.post(f"{URL}/done", token=token, json={"heading": "Skills", "version": 0})
    assert again.status_code == 409
    assert again.json()["detail"] == "A part is already being read. Wait for it first."
    missing = api.post(f"{URL}/done", token=token, json={"heading": "Hobbies", "version": 0})
    assert missing.status_code == 409
    # An empty skills part, once the first reading is over: no model, and its reading would
    # clear what the part held.
    with factory() as db:
        db.execute(sa.update(ProfileAbout).values(interpretation=None, run_id=None))
        db.commit()
    body = api.get(URL, token=token).json()
    emptied = put(api, token, body["version"], with_part(body["text"], "skills", ""))
    empty = api.post(
        f"{URL}/done", token=token, json={"heading": "Skills", "version": emptied.json()["version"]}
    )
    assert empty.status_code == 200
    assert empty.json()["reading"]["status"] == "ready"
    assert empty.json()["reading"]["lines"] == [
        "Years of experience: none",
        "2 skills removed from what I have: SQL, dbt",
    ]
    assert len(recorded.jobs) == 1


def test_a_fact_part_is_applied_by_code_at_done_and_undone(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    body = api.get(URL, token=token).json()
    text = body["text"].replace(
        "## Answers", "## Projects\n\n- Built the streaming platform\n\n## Answers"
    )
    saved = put(api, token, 0, text)
    assert [p["state"] for p in saved.json()["parts"]] == ["in_step", "new", "in_step"]
    count = len(revisions(factory, user_id))
    done = api.post(f"{URL}/done", token=token, json={"heading": "Projects", "version": 1})
    assert done.status_code == 200, done.text
    # No model, no key needed: the block is the part as written, applied at once.
    assert recorded.jobs == []
    assert done.json()["reading"] is None
    assert done.json()["undo"]["lines"] == ["1 fact block added: Projects"]
    assert done.json()["parts"][1]["state"] == "in_step"
    assert sections(factory, user_id)["facts"] == {
        "blocks": [{"title": "Projects", "body": "- Built the streaming platform"}]
    }
    assert revisions(factory, user_id)[count:] == ["facts"]
    assert api.post(f"{URL}/undo", token=token).status_code == 200
    assert sections(factory, user_id)["facts"] == {"blocks": []}


def run_done(factory: sessionmaker[Session], runtime: Runtime, user_id: uuid.UUID) -> uuid.UUID:
    with factory() as db:
        row = about._row(db, user_id, T0)
        about.save_text(db, user_id, row.version, with_part(row.text, "skills", EDITED), T0)
        made = about.start(db, user_id, "Skills", row.version, T0)
        db.commit()
    assert made is not None
    asyncio.run(about_agent.execute(runtime.context(), made))
    return made


def test_a_reading_keeps_what_the_checks_take_and_apply_asks_for_a_rescore(
    factory: sessionmaker[Session], migrated_url: str, api: Api, recorded: RecordingQueue
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = person(factory)
    give_key(factory, user_id, keyring=runtime.keyring)
    before = sections(factory, user_id)
    made = run_done(factory, runtime, user_id)
    with factory() as db:
        run = db.get(AgentRun, made)
        assert run is not None
        assert run.status is RunStatus.done
        assert run.model == about.MODEL_API_KEY
        events = db.scalars(
            sa.text("SELECT data::text FROM agent_events WHERE run_id = :r"), {"r": made}
        )
        assert not any("Airflow" in text for text in events)  # private: sizes only
    # Nothing is written by the reading.
    assert sections(factory, user_id) == before
    token = api.login_token(EMAIL)
    reading = api.get(URL, token=token).json()["reading"]
    assert reading["status"] == "ready"
    assert reading["can_apply"] is True
    assert reading["lines"] == LINES
    assert reading["refused"] == ["1 term of data: not written in this part."]
    count = len(revisions(factory, user_id))
    with factory() as db:
        revision = profile_service.get_profile(db, user_id, T0).revision
    applied = api.post(f"{URL}/apply", token=token)
    assert applied.status_code == 200, applied.text
    assert applied.json()["reading"] is None
    assert applied.json()["undo"]["lines"] == LINES
    assert applied.json()["parts"][0]["state"] == "in_step"
    skills = sections(factory, user_id)["skills"]
    assert skills["years_experience"] == 5
    assert skills["have"][0]["terms"] == ["SQL", "dbt", "Airflow"]
    assert [c["name"] for c in skills["none"]] == ["java"]
    assert revisions(factory, user_id)[count:] == ["skills"]  # one revision, one section
    with factory() as db:  # the rescore after a skills change, as for any save
        rescore = db.get(JobRescore, user_id)
        assert rescore is not None
        assert rescore.wanted_revision == revision + 1
    undone = api.post(f"{URL}/undo", token=token)
    assert undone.status_code == 200, undone.text
    assert sections(factory, user_id)["skills"] == before["skills"]
    assert undone.json()["undo"] is None
    assert undone.json()["parts"][0]["state"] == "edited"
    assert api.post(f"{URL}/undo", token=token).status_code == 409


def test_apply_is_refused_when_the_part_or_the_profile_changed_since(
    factory: sessionmaker[Session], migrated_url: str, api: Api, recorded: RecordingQueue
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = person(factory)
    give_key(factory, user_id, keyring=runtime.keyring)
    run_done(factory, runtime, user_id)
    token = api.login_token(EMAIL)
    body = api.get(URL, token=token).json()
    other = put(api, token, body["version"], body["text"] + "\nOne more line.\n")
    assert other.json()["reading"]["stale"] is False  # the last part is Answers
    text = other.json()["text"].replace("dbt, Airflow", "dbt, Airflow, Kafka")
    changed = put(api, token, other.json()["version"], text)
    assert changed.json()["reading"]["stale"] is True
    refused = api.post(f"{URL}/apply", token=token)
    assert refused.status_code == 409
    assert refused.json()["detail"] == about_router.REFUSED["part_changed"][1]
    put(api, token, changed.json()["version"], body["text"])  # back as it was read
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 9}, T0)
    refused = api.post(f"{URL}/apply", token=token)
    assert refused.status_code == 409
    assert refused.json()["detail"] == about_router.REFUSED["profile_changed"][1]
    assert sections(factory, user_id)["skills"]["years_experience"] == 9


def test_a_consent_in_the_answers_changes_only_on_apply_on_a_line_of_its_own(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    with factory() as db:
        row = about._row(db, user_id, T0)
        text = row.text.replace(
            "No, I do not consent to background checks.", "Yes, I consent to background checks."
        )
        about.save_text(db, user_id, 0, text, T0)
        part = next(p for p in about_text.split(text)[1] if p.kind == "answers")
        row.interpretation = {
            "key": part.key,
            "heading": part.heading,
            "kind": part.kind,
            "body": part.body,
            "digest": part.digest,
            "asked_at": T0.isoformat(),
            "status": "running",
            "run_id": None,
        }
        request = about._request(part, about._sections(db, user_id, T0), [])
        given = about_text.Interpretation(
            answers=about_text.AnswersIn(
                notice_period="Three months.",
                background_check_consent=about_text.SaidIn(
                    value=True, quote="Yes, I consent to background checks."
                ),
            )
        )
        about._record(row, request, given, T0)
        db.commit()
    reading = api.get(URL, token=token).json()["reading"]
    assert reading["lines"] == ["Background checks: no to yes"]
    assert sections(factory, user_id)["answers"]["background_check_consent"] is False
    assert api.post(f"{URL}/apply", token=token).status_code == 200
    assert sections(factory, user_id)["answers"]["background_check_consent"] is True
    assert sections(factory, user_id)["answers"]["notice_period"] == "Three months."


def test_rewrite_puts_a_part_back_in_step_and_adds_blocks_no_part_holds(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    api.get(URL, token=token)
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 6}, T0)
    save(factory, user_id, "facts", {"blocks": [{"title": "Projects", "body": "Built it."}]}, T0)
    body = api.get(URL, token=token).json()
    assert body["missing_blocks"] == ["Projects"]
    written = api.post(f"{URL}/rewrite", token=token, json={"heading": "Skills", "version": 0})
    assert written.status_code == 200, written.text
    assert "Years of experience: 6" in written.json()["text"]
    assert written.json()["parts"][0]["state"] == "in_step"
    added = api.post(f"{URL}/rewrite", token=token, json={"version": written.json()["version"]})
    assert [p["heading"] for p in added.json()["parts"]] == ["Skills", "Projects", "Answers"]
    assert added.json()["missing_blocks"] == []


def test_a_run_nobody_waits_for_never_calls_the_model(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = person(factory)
    give_key(factory, user_id, keyring=runtime.keyring)
    with factory() as db:
        about._row(db, user_id, T0)
        made = about.start(db, user_id, "Skills", 0, T0)
        db.execute(sa.update(ProfileAbout).values(run_id=None))
        db.commit()
    assert made is not None
    asyncio.run(about_agent.execute(runtime.context(), made))
    assert runtime.backends.made == []


def test_submit_keeps_one_reading_per_run_and_says_what_to_fix() -> None:
    run_id = uuid.uuid4()
    text, error = about_agent.submit(run_id, {"categories": [{"bucket": "have"}]})
    assert error
    assert text.startswith("Not stored: categories.0.name")
    assert about_agent.submit(run_id, {"gates": []}) == (about_agent.STORED, False)
    assert about_agent.submit(run_id, {"gates": []}) == (about_agent.SUBMITTED_ONCE, True)
    assert about_agent.take(run_id) == about_text.Interpretation(gates=[])
    assert about_agent.submit(None, {}) == (about_agent.NO_RUN, True)
    found = about_agent.PROFILE_INTERPRET
    assert found.internal
    assert found.private_io
    assert not found.live_text
    assert found.tools == (about_agent.SUBMIT,)
    assert found.max_turns == about.MAX_TURNS
    assert found.model_for(CredentialKind.anthropic_api_key) == about.MODEL_API_KEY
