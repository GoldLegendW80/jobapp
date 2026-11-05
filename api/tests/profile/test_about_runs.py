"""The "About you" document with real Postgres (app/profile/about.py, about_agent.py,
about_router.py): the conversion once, the autosave, Done on the fake backend (the recorded
stream `profile_interpret`: no model is called, the key is a made up value of the right shape),
what the app understood, Apply (one revision per section, locked facts only there), Undo, the
states without a key and the cost line."""

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
from app.profile import about, about_agent, about_text
from app.profile import service as profile_service
from app.profile.models import Profile, ProfileAbout, ProfileRevision
from tests.agents.conftest import new_key
from tests.identity.api_helpers import Api, make_user
from tests.profile.test_titles_runs import (
    RecordingQueue,
    Runtime,
    give_key,
    save,
)

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
        },
        {
            "key": "example-data",
            "organisation": "Example Data",
            "title": "Analyst",
            "start": "2018",
            "end": "2021",
            "location": "Paris",
            "contract": None,
        },
    ],
    "education": [],
}
SKILLS = {
    "years_experience": 4,
    "have": [{"name": "data", "terms": ["SQL", "dbt"], "advanced_regex": False}],
}
# What the person types in the Experience part before Done (the stream reads this).
EDITED = (
    "### Senior data engineer, Example Robotics\nMarch 2021 to now · Lyon · CDI\n\n"
    "### Analyst, Example Data\n2018 to 2021 · Paris\n\n"
    "### Intern, Example Labs\n2017 to 2018 · Grenoble · internship"
)


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def person(factory: sessionmaker[Session], email: str = EMAIL) -> uuid.UUID:
    user_id = make_user(factory, email=email, name=email.split("@")[0]).id
    save(factory, user_id, "experience", EXPERIENCE)
    save(factory, user_id, "skills", SKILLS)
    return user_id


def signed(factory: sessionmaker[Session], api: Api, email: str = EMAIL) -> tuple[uuid.UUID, str]:
    return person(factory, email), api.login_token(email)


def put(api: Api, token: str, version: int, text: str) -> Any:
    return api.request("PUT", URL, token=token, json={"version": version, "text": text})


def edited(text: str) -> str:
    preamble, parts = about_text.split(text)
    parts = [about_text.Part(p.heading, EDITED) if p.kind == "experience" else p for p in parts]
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


def test_the_first_read_writes_the_document_once_and_changes_no_section(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    before = revisions(factory, user_id)
    first = api.get(URL, token=token)
    assert first.status_code == 200, first.text
    body = first.json()
    assert first.headers["cache-control"] == "no-store"
    assert body["text"].startswith("## Experience\n\n### Data engineer, Example Robotics\n")
    assert body["version"] == 0
    assert [(p["heading"], p["kind"], p["state"]) for p in body["parts"]] == [
        ("Experience", "experience", "in_step"),
        ("Education", "education", "in_step"),
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
    assert [p["state"] for p in again["parts"]][2] == "elsewhere"
    # Another person reads their own document only.
    make_user(factory, email=OTHER, name="other")
    other = api.login_token(OTHER)
    assert "Example Robotics" not in api.get(URL, token=other).json()["text"]


def test_the_text_saves_as_typed_and_a_stale_version_gets_the_current_one(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    _, token = signed(factory, api)
    text = api.get(URL, token=token).json()["text"]
    saved = put(api, token, 0, edited(text))
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["parts"][0]["state"] == "edited"
    stale = put(api, token, 0, text)
    assert stale.status_code == 409
    assert stale.json()["detail"] == about_router_stale()
    assert stale.json()["current"]["text"] == edited(text)
    too_long = put(api, token, 1, "x" * (about_text.TEXT_MAX + 1))
    assert too_long.status_code == 422
    control = put(api, token, 1, "## Skills\n\u0007")
    assert control.status_code == 422


def about_router_stale() -> str:
    from app.profile import about_router

    return about_router.STALE


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
    other_id, other = signed(factory, api, OTHER)
    give_key(factory, other_id, CredentialKind.claude_subscription)
    estimate = api.get(URL, token=other).json()["parts"][0]["estimate"]
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
    # An empty part, once the first reading is over: no model, its reading says it states
    # nothing, and Apply would clear what it held.
    with factory() as db:
        db.execute(sa.update(ProfileAbout).values(interpretation=None, run_id=None))
        db.commit()
    empty = api.post(f"{URL}/done", token=token, json={"heading": "Answers", "version": 0})
    assert empty.status_code == 200
    assert empty.json()["reading"]["status"] == "ready"
    assert empty.json()["reading"]["can_apply"] is False
    assert empty.json()["reading"]["lines"] == [about.NOTHING]
    assert len(recorded.jobs) == 1


def test_a_queue_that_is_down_fails_the_reading_and_gives_the_click_back(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    left = api.get(URL, token=token).json()["dones_left"]
    recorded.down = True
    down = api.post(f"{URL}/done", token=token, json={"heading": "Skills", "version": 0})
    assert down.status_code == 503
    body = api.get(URL, token=token).json()
    assert body["reading"]["status"] == "failed"
    assert body["reading"]["error"] == about.ERRORS["queue_down"]
    assert body["dones_left"] == left


def run_done(factory: sessionmaker[Session], runtime: Runtime, user_id: uuid.UUID) -> uuid.UUID:
    with factory() as db:
        row = about._row(db, user_id, T0)
        text = edited(row.text)
        about.save_text(db, user_id, row.version, text, T0)
        made = about.start(db, user_id, "Experience", row.version, T0)
        db.commit()
    assert made is not None
    asyncio.run(about_agent.execute(runtime.context(), made))
    return made


def test_a_reading_keeps_what_the_checks_take_and_changes_nothing_before_apply(
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
        assert not any("Example Labs" in text for text in events)  # private: sizes only
    # Locked facts change only on Apply: nothing is written by the reading.
    assert sections(factory, user_id) == before
    token = api.login_token(EMAIL)
    reading = api.get(URL, token=token).json()["reading"]
    assert reading["status"] == "ready"
    assert reading["can_apply"] is True
    assert reading["lines"] == [
        "1 job added: Intern at Example Labs, 2017 to 2018",
        "1 job updated: Senior data engineer at Example Robotics, March 2021 to now",
    ]
    assert reading["refused"] == [
        "A job at Example Space: its dates, title or place are not written that way in this part."
    ]
    count = len(revisions(factory, user_id))
    applied = api.post(f"{URL}/apply", token=token)
    assert applied.status_code == 200, applied.text
    assert applied.json()["reading"] is None
    assert applied.json()["undo"]["lines"] == reading["lines"]
    assert applied.json()["parts"][0]["state"] == "in_step"
    after = sections(factory, user_id)["experience"]["employers"]
    assert [(e["key"], e["title"]) for e in after] == [
        ("example-robotics", "Senior data engineer"),
        ("example-data", "Analyst"),
        ("example-labs", "Intern"),
    ]
    assert revisions(factory, user_id)[count:] == ["experience"]  # one revision, one section
    undone = api.post(f"{URL}/undo", token=token)
    assert undone.status_code == 200, undone.text
    assert sections(factory, user_id)["experience"] == before["experience"]
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
    changed = put(api, token, body["version"], body["text"] + "\nOne more line.\n")
    assert changed.json()["reading"]["stale"] is False  # the last part is Answers
    text = changed.json()["text"].replace("Grenoble", "Grenoble, France")
    changed = put(api, token, changed.json()["version"], text)
    assert changed.json()["reading"]["stale"] is True
    refused = api.post(f"{URL}/apply", token=token)
    assert refused.status_code == 409
    assert (
        refused.json()["detail"]
        == "You changed this part since Done. Click Done again to see what changes."
    )
    put(api, token, changed.json()["version"], body["text"])  # back as it was read
    save(factory, user_id, "experience", {**EXPERIENCE, "education": []} | {"employers": []}, T0)
    refused = api.post(f"{URL}/apply", token=token)
    assert refused.status_code == 409
    assert (
        refused.json()["detail"]
        == "Your profile changed since. Click Done again to see what changes."
    )
    assert sections(factory, user_id)["experience"]["employers"] == []


def test_a_skills_part_applied_asks_for_a_rescore_like_any_save(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    with factory() as db:
        row = about._row(db, user_id, T0)
        text = row.text.replace("- data: SQL, dbt", "- data: SQL, dbt, Airflow")
        about.save_text(db, user_id, 0, text, T0)
        part = next(p for p in about_text.split(text)[1] if p.kind == "skills")
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
            years_experience=4,
            categories=[
                about_text.CategoryIn(bucket="have", name="data", terms=["SQL", "dbt", "Airflow"])
            ],
            gates=[],
        )
        about._record(row, request, given, T0)
        db.commit()
    with factory() as db:
        revision = profile_service.get_profile(db, user_id, T0).revision
    body = api.get(URL, token=token).json()
    assert body["reading"]["lines"] == ["1 skill added to what I have: Airflow"]
    assert api.post(f"{URL}/apply", token=token).status_code == 200
    with factory() as db:
        rescore = db.get(JobRescore, user_id)
        assert rescore is not None
        assert rescore.wanted_revision == revision + 1
    assert sections(factory, user_id)["skills"]["have"][0]["terms"] == ["SQL", "dbt", "Airflow"]


def test_rewrite_puts_a_part_back_in_step_and_adds_blocks_no_part_holds(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    body = api.get(URL, token=token).json()
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 6}, T0)
    save(factory, user_id, "facts", {"blocks": [{"title": "Projects", "body": "Built it."}]}, T0)
    body = api.get(URL, token=token).json()
    assert body["missing_blocks"] == ["Projects"]
    written = api.post(f"{URL}/rewrite", token=token, json={"heading": "Skills", "version": 0})
    assert written.status_code == 200, written.text
    assert "Years of experience: 6" in written.json()["text"]
    assert written.json()["parts"][2]["state"] == "in_step"
    added = api.post(f"{URL}/rewrite", token=token, json={"version": written.json()["version"]})
    assert [p["heading"] for p in added.json()["parts"]] == [
        "Experience",
        "Education",
        "Skills",
        "Projects",
        "Answers",
    ]
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
    text, error = about_agent.submit(run_id, {"employers": [{"organisation": "x"}]})
    assert error
    assert text.startswith("Not stored: employers.0.title")
    assert about_agent.submit(run_id, {"blocks": []}) == (about_agent.STORED, False)
    assert about_agent.submit(run_id, {"blocks": []}) == (about_agent.SUBMITTED_ONCE, True)
    assert about_agent.take(run_id) == about_text.Interpretation(blocks=[])
    assert about_agent.submit(None, {}) == (about_agent.NO_RUN, True)
    found = about_agent.PROFILE_INTERPRET
    assert found.internal
    assert found.private_io
    assert not found.live_text
    assert found.tools == (about_agent.SUBMIT,)
    assert found.max_turns == about.MAX_TURNS
    assert found.model_for(CredentialKind.anthropic_api_key) == about.MODEL_API_KEY
    assert new_key()  # the fake key helper stays importable
