"""The job titles agent's runs (app/profile/titles.py, agents.py, titles_router.py): who may
start one and how often, the triggers the tick reads, a whole run on the fake backend (the
recorded streams `profile_titles*`: no model is called, the key is a made up value of the right
shape), and the routes. Real Postgres."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials, definitions
from app.agents.crypto import Keyring
from app.agents.models import (
    AgentRun,
    CredentialKind,
    Proposal,
    ProposalDecider,
    ProposalStatus,
    RunStatus,
)
from app.agents.runner import RunContext
from app.documents import writer_cv
from app.profile import agents, titles
from app.profile import service as profile_service
from app.profile.models import TitleSuggestions
from app.profile.schemas import validate_section
from tests.agents.conftest import API_KEY, OAUTH, new_key
from tests.agents.run_helpers import Backends
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock, make_user

EMAIL = "e2e-titles@example.org"
OTHER = "e2e-titles-other@example.org"
URL = "/api/profile/title-suggestions"
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

SKILLS = {
    "years_experience": 4,
    "have": [{"name": "data", "terms": ["sql", "dbt", "python"], "advanced_regex": False}],
    "adjacent": [],
    "none": [],
    "gates": [],
}
SEARCH = {
    "title_include": {"terms": ["Data engineer"], "advanced_regex": False},
    "title_exclude": {"terms": ["intern"], "advanced_regex": False},
    "location": {"terms": ["Remote"], "advanced_regex": False},
    "never_apply": ["No consulting firms: consultant, ESN"],
}


def person(factory: sessionmaker[Session], email: str = EMAIL) -> uuid.UUID:
    return make_user(factory, email=email, name=email.split("@")[0]).id


def save(
    factory: sessionmaker[Session], user_id: uuid.UUID, name: str, data: Any, at: datetime = T0
) -> None:
    with factory() as db:
        profile = profile_service.get_profile(db, user_id, at)
        clean = validate_section(name, data)
        profile_service.save_section(db, user_id, name, clean, profile.revision, at)
        db.commit()


def give_key(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    kind: CredentialKind = CredentialKind.anthropic_api_key,
    keyring: Keyring | None = None,
) -> None:
    with factory() as db:
        secret = API_KEY if kind is CredentialKind.anthropic_api_key else OAUTH
        credentials.save(db, keyring or Keyring.from_text(new_key()), user_id, kind, secret, T0)
        db.commit()


def ready(factory: sessionmaker[Session], email: str = EMAIL) -> uuid.UUID:
    """A person with a key, skills and a search."""
    user_id = person(factory, email)
    give_key(factory, user_id)
    save(factory, user_id, "search", SEARCH)
    save(factory, user_id, "skills", SKILLS)
    return user_id


def start(factory: sessionmaker[Session], user_id: uuid.UUID, at: datetime, auto: bool) -> Any:
    with factory() as db:
        try:
            made = titles.start(db, user_id, at, auto=auto)
        except titles.RefusedError as refused:
            db.rollback()
            return refused.code
        db.commit()
        return made


def end(factory: sessionmaker[Session], run_id: uuid.UUID, *entries: str) -> None:
    """The run done, with these titles submitted (none: it gave nothing)."""
    with factory() as db:
        db.execute(sa.update(AgentRun).where(AgentRun.id == run_id).values(status=RunStatus.done))
        run = db.get(AgentRun, run_id)
        assert run is not None
        request = titles.request_of(titles._sections(db, run.user_id))
        submitted = [titles.Submitted(title=t, reason="Your SQL work.") for t in entries]
        titles.finish(db, run_id, request, submitted, T0)
        db.commit()


def row(factory: sessionmaker[Session], user_id: uuid.UUID) -> TitleSuggestions | None:
    with factory() as db:
        return db.get(TitleSuggestions, user_id)


def runs_of(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[AgentRun]:
    with factory() as db:
        return list(db.scalars(sa.select(AgentRun).where(AgentRun.user_id == user_id)))


# --- the definition ------------------------------------------------------------------------------


def test_the_definition_is_internal_private_bounded_and_has_one_tool() -> None:
    found = definitions.AGENTS[titles.NAME]
    assert found is agents.PROFILE_TITLES
    assert found.internal
    assert found.private_io
    assert not found.live
    assert found.tools == ("submit_titles",)
    assert found.max_turns == 3
    assert found.wall_time_s == 60.0
    assert found.model_for(CredentialKind.anthropic_api_key) == titles.MODEL_API_KEY
    assert found.model_for(CredentialKind.claude_subscription) == titles.MODEL
    assert titles.MODEL_API_KEY == writer_cv.MODEL_API_KEY
    assert titles.MODEL == writer_cv.MODEL
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable(titles.NAME, "test")
    assert titles.NAME not in [d.name for d in definitions.listed("test")]
    with pytest.raises(definitions.InvalidInputError):
        found.validate_input({"request": "a" * 32, "token_budget": 1, "summary": "SQL"})
    with pytest.raises(LookupError):
        found.prompt({"request": "b" * 32}, None)  # nobody waits: no model call


def test_submit_keeps_one_list_per_run_and_says_what_to_fix() -> None:
    run_id = uuid.uuid4()
    text, error = agents.submit(run_id, {"titles": [{"title": "", "reason": "x"}]})
    assert error
    assert text.startswith("Not stored: titles.0.title")
    assert agents.submit(run_id, {"titles": [{"title": "Analytics engineer"}]}) == (
        agents.STORED,
        False,
    )
    assert agents.submit(run_id, {"titles": []}) == (agents.SUBMITTED_ONCE, True)
    assert [one.title for one in agents.take(run_id)] == ["Analytics engineer"]
    assert agents.take(run_id) == ()
    assert agents.submit(None, {"titles": []}) == (agents.NO_RUN, True)


# --- who may start a run, and how often ----------------------------------------------------------


def test_no_run_without_a_model_credential(factory: sessionmaker[Session]) -> None:
    user_id = person(factory)
    save(factory, user_id, "skills", SKILLS)
    assert start(factory, user_id, T0, auto=False) == "no_credential"
    assert start(factory, user_id, T0, auto=True) == "no_credential"
    assert runs_of(factory, user_id) == []


def test_no_run_without_a_cv_or_skills_to_read(factory: sessionmaker[Session]) -> None:
    user_id = person(factory)
    give_key(factory, user_id)
    assert start(factory, user_id, T0, auto=False) == "nothing_to_read"
    assert runs_of(factory, user_id) == []


def test_one_run_at_a_time(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    first = start(factory, user_id, T0, auto=False)
    assert isinstance(first, uuid.UUID)
    assert start(factory, user_id, T0, auto=False) == "running"
    assert start(factory, user_id, T0, auto=True) == "running"
    # A run that never came back stops counting after STALE_AFTER.
    later = T0 + titles.STALE_AFTER + timedelta(seconds=1)
    assert isinstance(start(factory, user_id, later, auto=False), uuid.UUID)


def test_more_ideas_is_bounded_per_day(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    for _ in range(titles.ASKS_PER_DAY):
        made = start(factory, user_id, T0, auto=False)
        assert isinstance(made, uuid.UUID)
        end(factory, made, "Analytics engineer")
    assert titles.ASKS_PER_DAY == 3
    assert start(factory, user_id, T0 + timedelta(hours=1), auto=False) == "asked_enough"
    tomorrow = datetime(2026, 11, 6, 0, 0, 1, tzinfo=UTC)
    assert isinstance(start(factory, user_id, tomorrow, auto=False), uuid.UUID)


def test_an_automatic_run_is_at_most_daily_and_only_on_a_change(
    factory: sessionmaker[Session],
) -> None:
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=True)
    assert isinstance(made, uuid.UUID)
    end(factory, made, "Analytics engineer")
    almost = T0 + titles.AUTO_EVERY - timedelta(seconds=1)
    assert start(factory, user_id, almost, auto=True) == "not_due"
    # A click is not bound by the automatic run of the day.
    after = T0 + titles.AUTO_EVERY + timedelta(seconds=1)
    assert start(factory, user_id, after, auto=True) == "unchanged"
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 5}, after)
    assert isinstance(start(factory, user_id, after, auto=True), uuid.UUID)


# --- the end of a run ----------------------------------------------------------------------------


def test_finish_keeps_what_passes_and_says_why_nothing_came(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics engineer", "Data engineer", "Security engineer")
    found = row(factory, user_id)
    assert found is not None
    assert found.run_id is None
    assert found.error is None
    assert [one["title"] for one in found.suggestions] == ["Analytics engineer"]
    assert found.refreshed_at == T0
    assert found.basis is not None
    made = start(factory, user_id, T0, auto=False)
    end(factory, made)
    found = row(factory, user_id)
    assert found is not None
    assert found.error == "no_answer"
    # The answer before stays shown.
    assert [one["title"] for one in found.suggestions] == ["Analytics engineer"]


def test_a_failed_run_is_said_and_a_newer_ask_is_left_alone(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=False)
    with factory() as db:
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == made)
            .values(status=RunStatus.failed, error="token_limit")
        )
        titles.finish(db, made, None, (), T0)
        db.commit()
    found = row(factory, user_id)
    assert found is not None
    assert found.error in {"token_budget", "failed"}
    newer = start(factory, user_id, T0, auto=False)
    with factory() as db:
        titles.finish(db, made, None, (), T0)  # the old run's end, late
        db.commit()
    assert row(factory, user_id).run_id == newer  # type: ignore[union-attr]


# --- the triggers --------------------------------------------------------------------------------


def due(factory: sessionmaker[Session], at: datetime) -> list[uuid.UUID]:
    with factory() as db:
        return titles.due(db, at)


def test_a_skills_save_makes_a_person_due_once(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    assert due(factory, T0) == [user_id]
    with factory() as db:
        titles.seen(db, user_id, T0)
        db.commit()
    assert due(factory, T0 + timedelta(minutes=1)) == []
    # A search save is no trigger; a later skills save is.
    save(factory, user_id, "search", SEARCH, T0 + timedelta(minutes=2))
    assert due(factory, T0 + timedelta(minutes=3)) == []
    save(factory, user_id, "skills", SKILLS, T0 + timedelta(minutes=4))
    assert due(factory, T0 + timedelta(minutes=5)) == [user_id]


def test_an_accepted_fill_item_makes_a_person_due(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    with factory() as db:
        titles.seen(db, user_id, T0 + timedelta(minutes=1))
        db.add(
            Proposal(
                user_id=user_id,
                target="profile",
                kind="profile_item",
                payload={},
                status=ProposalStatus.accepted,
                decided_by=ProposalDecider.user,
                decided_at=T0 + timedelta(minutes=2),
            )
        )
        db.add(
            Proposal(
                user_id=user_id,
                target="profile",
                kind="profile_item",
                payload={},
                status=ProposalStatus.rejected,
                decided_by=ProposalDecider.user,
                decided_at=T0 + timedelta(minutes=3),
            )
        )
        db.commit()
    assert due(factory, T0 + timedelta(minutes=4)) == [user_id]


def test_no_one_is_due_without_a_key_or_within_the_day(factory: sessionmaker[Session]) -> None:
    keyless = person(factory, OTHER)
    save(factory, keyless, "skills", SKILLS)
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=True)
    end(factory, made, "Analytics engineer")
    save(factory, user_id, "skills", {**SKILLS, "years_experience": 6}, T0 + timedelta(hours=1))
    assert due(factory, T0 + timedelta(hours=2)) == []
    # The trigger waits for the day to pass, then the tick takes it.
    assert due(factory, T0 + titles.AUTO_EVERY + timedelta(minutes=1)) == [user_id]


class FakeRedis:
    def __init__(self, down: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.down = down

    async def enqueue_job(self, name: str, *args: Any, **options: Any) -> object:
        if self.down:
            raise ConnectionError("down")
        self.jobs.append((name, args, options))
        return object()


def test_the_tick_starts_and_queues_the_due_runs(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    unreadable = person(factory, OTHER)
    give_key(factory, unreadable)
    save(factory, unreadable, "skills", {**SKILLS, "have": []})
    redis = FakeRedis()
    started = asyncio.run(agents.tick(factory, T0 + timedelta(minutes=1), redis))
    assert started == 1
    [run] = runs_of(factory, user_id)
    assert run.agent == titles.NAME
    assert run.status is RunStatus.queued
    assert run.input["token_budget"] == titles.TOKEN_BUDGET
    assert set(run.input) == {"request", "token_budget"}  # no text of the person
    assert redis.jobs == [
        (
            agents.JOB,
            (str(run.id),),
            {"_job_id": agents.job_id(run.id), "_queue_name": queues.AGENTS},
        )
    ]
    # Nothing to read settles the trigger without a run.
    assert runs_of(factory, unreadable) == []
    assert row(factory, unreadable).seen_at == T0 + timedelta(minutes=1)  # type: ignore[union-attr]
    assert asyncio.run(agents.tick(factory, T0 + timedelta(minutes=2), redis)) == 0


def test_a_queue_that_refuses_the_tick_fails_the_run_and_frees_the_day(
    factory: sessionmaker[Session],
) -> None:
    user_id = ready(factory)
    assert asyncio.run(agents.tick(factory, T0, FakeRedis(down=True))) == 0
    [run] = runs_of(factory, user_id)
    assert run.status is RunStatus.failed
    found = row(factory, user_id)
    assert found is not None
    assert found.error == "queue_down"
    assert found.auto_at is None


# --- a whole run on the fake backend -------------------------------------------------------------


class Runtime:
    def __init__(self, factory: sessionmaker[Session], migrated_url: str) -> None:
        key = new_key()
        self.factory = factory
        self.settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
        self.keyring = Keyring.from_text(key)
        self.backends = Backends(speed=0.0)

    def context(self) -> RunContext:
        return RunContext(
            settings=self.settings,
            sessions=self.factory,
            clock=lambda: T0,
            keyring=self.keyring,
            backend_factory=self.backends,
            cancel_poll_s=0.02,
            interrupt_grace_s=1.0,
        )


def run_once(
    factory: sessionmaker[Session], runtime: Runtime, user_id: uuid.UUID, script: str | None = None
) -> uuid.UUID:
    made = start(factory, user_id, T0, auto=False)
    assert isinstance(made, uuid.UUID)
    if script is not None:
        with factory() as db:
            run = db.get(AgentRun, made)
            assert run is not None
            run.input = {**run.input, "script": script}
            db.commit()
    asyncio.run(agents.execute(runtime.context(), made))
    return made


def keyed(factory: sessionmaker[Session], runtime: Runtime) -> uuid.UUID:
    user_id = person(factory)
    give_key(factory, user_id, keyring=runtime.keyring)
    save(factory, user_id, "search", SEARCH)
    save(factory, user_id, "skills", SKILLS)
    return user_id


def test_a_run_stores_only_what_the_checks_keep(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = keyed(factory, runtime)
    made = run_once(factory, runtime, user_id)
    [run] = runs_of(factory, user_id)
    assert run.id == made
    assert run.status is RunStatus.done
    assert run.model == titles.MODEL_API_KEY
    found = row(factory, user_id)
    assert found is not None
    assert found.run_id is None
    assert found.error is None
    assert found.suggestions == [
        {
            "title": "Analytics engineer",
            "reason": "Your SQL and dbt work matches this role.",
            "language": "en",
        },
        {
            "title": "Data platform engineer",
            "reason": "You built pipelines and the platform under them.",
            "language": "en",
        },
        {
            "title": "Machine learning engineer",
            "reason": "Your Python work, and your model serving, fit this role.",
            "language": "en",
        },
    ]
    # A private run: no title in the events.
    with factory() as db:
        events = db.scalars(
            sa.text("SELECT data::text FROM agent_events WHERE run_id = :r"), {"r": made}
        )
        assert not any("Analytics" in text for text in events)


def test_a_run_for_french_places_stores_french_titles(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = keyed(factory, runtime)
    save(factory, user_id, "search", {**SEARCH, "location": {"terms": ["Lyon", "France"]}})
    backend_prompts: list[str] = []
    original = titles.prompt_for

    def spy(request: titles.Request) -> str:
        backend_prompts.append(original(request))
        return backend_prompts[-1]

    agents_prompt = titles.prompt_for
    titles.prompt_for = spy  # type: ignore[assignment]
    try:
        run_once(factory, runtime, user_id, script="profile_titles_french")
    finally:
        titles.prompt_for = agents_prompt  # type: ignore[assignment]
    assert '"Lyon"' in backend_prompts[0]
    assert '"France"' in backend_prompts[0]
    found = row(factory, user_id)
    assert found is not None
    assert [(one["title"], one["language"]) for one in found.suggestions] == [
        ("Ingénieur données", "fr"),
        ("Analyste de données", "fr"),
        ("Ingénieur analytique", "fr"),
    ]


def test_a_run_nobody_waits_for_never_calls_the_model(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    runtime = Runtime(factory, migrated_url)
    user_id = keyed(factory, runtime)
    made = start(factory, user_id, T0, auto=False)
    with factory() as db:  # the person's row asks for another run since
        db.execute(sa.update(TitleSuggestions).values(run_id=None))
        db.commit()
    asyncio.run(agents.execute(runtime.context(), made))
    assert runtime.backends.made == []
    assert runs_of(factory, user_id)[0].status is RunStatus.failed


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
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def signed(factory: sessionmaker[Session], api: Api, email: str = EMAIL) -> tuple[uuid.UUID, str]:
    user_id = person(factory, email)
    return user_id, api.login_token(email)


def test_get_says_what_is_missing_and_never_calls_a_model(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    body = api.get(URL, token=token).json()
    assert body["status"] == "idle"
    assert body["needs"] == "model_key"
    assert body["suggestions"] == []
    assert body["estimate"] is None
    give_key(factory, user_id)
    body = api.get(URL, token=token).json()
    assert body["needs"] == "cv"
    save(factory, user_id, "skills", SKILLS)
    body = api.get(URL, token=token).json()
    assert body["needs"] is None
    assert body["asks_left"] == 3
    assert body["estimate"]["cost"]["currency"] == "USD"
    assert body["cost_note"] is not None
    assert recorded.jobs == []
    assert runs_of(factory, user_id) == []
    assert api.get(URL).status_code == 401


def test_on_a_subscription_nothing_is_said_about_money(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id, CredentialKind.claude_subscription)
    body = api.get(URL, token=token).json()
    assert body["estimate"]["cost"] is None
    assert body["cost_note"] is None
    assert body["estimate"]["tokens"]["low"] > 0


def test_more_ideas_starts_one_run_and_counts_the_click(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    assert (
        api.post(URL, token=token).json()["detail"] == "Add a model key on the Account page first."
    )
    give_key(factory, user_id)
    response = api.post(URL, token=token)
    assert response.status_code == 409
    assert "CV or your skills" in response.json()["detail"]
    save(factory, user_id, "skills", SKILLS)
    response = api.post(URL, token=token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["asks_left"] == 2
    [run] = runs_of(factory, user_id)
    assert recorded.jobs == [(agents.JOB, (str(run.id),), queues.AGENTS, agents.job_id(run.id))]
    again = api.post(URL, token=token)
    assert again.status_code == 409
    assert again.json()["detail"] == "Suggestions are already on their way."


def test_more_ideas_past_the_day_s_clicks_is_refused(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "skills", SKILLS)
    for _ in range(3):
        assert api.post(URL, token=token).status_code == 200
        end(factory, _last_run(factory, user_id))
    response = api.post(URL, token=token)
    assert response.status_code == 429
    assert (
        response.json()["detail"] == "You asked for more ideas 3 times today. Try again tomorrow."
    )


def _last_run(factory: sessionmaker[Session], user_id: uuid.UUID) -> uuid.UUID:
    found = row(factory, user_id)
    assert found is not None
    assert found.run_id is not None
    return found.run_id


def test_a_queue_that_is_down_fails_the_run_and_gives_the_click_back(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "skills", SKILLS)
    recorded.down = True
    response = api.post(URL, token=token)
    assert response.status_code == 503
    body = api.get(URL, token=token).json()
    assert body["status"] == "failed"
    assert body["error"] == titles.ERRORS["queue_down"]
    assert body["asks_left"] == 3
    assert runs_of(factory, user_id)[0].status is RunStatus.failed


def test_shown_suggestions_follow_the_search_as_saved_now(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "search", SEARCH)
    save(factory, user_id, "skills", SKILLS)
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics engineer", "Data platform engineer")
    body = api.get(URL, token=token).json()
    assert [one["title"] for one in body["suggestions"]] == [
        "Analytics engineer",
        "Data platform engineer",
    ]
    wanted = {**SEARCH, "title_include": {"terms": ["Data engineer", "analytics engineers"]}}
    save(factory, user_id, "search", wanted, T0 + timedelta(minutes=1))
    body = api.get(URL, token=token).json()
    assert [one["title"] for one in body["suggestions"]] == ["Data platform engineer"]
    # Another person reads nothing of it.
    _, other = signed(factory, api, OTHER)
    assert api.get(URL, token=other).json()["suggestions"] == []


def test_a_run_that_never_came_back_reads_as_failed(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue, clock: Clock
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "skills", SKILLS)
    assert api.post(URL, token=token).status_code == 200
    clock.advance(seconds=titles.STALE_AFTER.total_seconds() + 1)
    body = api.get(URL, token=token).json()
    assert body["status"] == "failed"
    assert body["error"] == titles.ERRORS["lost"]
