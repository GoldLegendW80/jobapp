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


def test_no_run_with_nothing_to_read_and_a_message_alone_starts_one(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    give_key(factory, user_id)
    assert start(factory, user_id, T0, auto=False) == "nothing_to_read"
    assert runs_of(factory, user_id) == []
    # An empty profile, no CV: one message is enough, and it is kept with the row.
    with factory() as db:
        made = titles.start(db, user_id, T0, auto=False, message="Move into product")
        db.commit()
    found = row(factory, user_id)
    assert found is not None
    assert found.messages == [{"text": "Move into product", "at": T0.isoformat()}]
    with factory() as db:
        key_and_request = titles.run_request(db, made)
    assert key_and_request is not None
    assert key_and_request[1].messages == ("Move into product",)
    assert '"Move into product"' in titles.prompt_for(key_and_request[1])


def test_a_message_is_kept_only_when_its_run_starts_and_the_last_five_stay(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    give_key(factory, user_id)
    for day in range(titles.MESSAGES_MAX + 1):
        at = T0 + timedelta(days=day)
        with factory() as db:
            made = titles.start(db, user_id, at, auto=False, message=f"Wish {day}")
            db.commit()
        end(factory, made, f"Data role {day}")
    found = row(factory, user_id)
    assert found is not None
    assert [one["text"] for one in found.messages] == [
        f"Wish {day}" for day in range(1, titles.MESSAGES_MAX + 1)
    ]
    # A refused ask keeps nothing: one is on its way.
    with factory() as db:
        titles.start(db, user_id, T0 + timedelta(days=9), auto=False)
        db.commit()
    with factory() as db, pytest.raises(titles.RefusedError):
        titles.start(db, user_id, T0 + timedelta(days=9), auto=False, message="Lost")
    assert "Lost" not in str(row(factory, user_id).messages)  # type: ignore[union-attr]
    # The automatic refresh reads them too.
    with factory() as db:
        request = titles._request(db, user_id, db.get(TitleSuggestions, user_id))
    assert request is not None
    assert request.messages[-1] == f"Wish {titles.MESSAGES_MAX}"


def test_one_run_at_a_time(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    first = start(factory, user_id, T0, auto=False)
    assert isinstance(first, uuid.UUID)
    assert start(factory, user_id, T0, auto=False) == "running"
    assert start(factory, user_id, T0, auto=True) == "running"
    # A run that never came back stops counting after STALE_AFTER.
    later = T0 + titles.STALE_AFTER + timedelta(seconds=1)
    assert isinstance(start(factory, user_id, later, auto=False), uuid.UUID)


def test_more_ideas_has_no_daily_limit(factory: sessionmaker[Session]) -> None:
    # Owner, 2026-11-06: "I don't want day limit for more ideas". One run at a time still.
    user_id = ready(factory)
    for _ in range(10):
        made = start(factory, user_id, T0, auto=False)
        assert isinstance(made, uuid.UUID)
        end(factory, made, "Analytics engineer")
    assert not hasattr(titles, "ASKS_PER_DAY")


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
    end(factory, made, "Analytics engineer", "Data engineer", "Data intern")
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


def test_more_ideas_adds_to_the_ideas_shown_and_says_when_nothing_new_came(
    factory: sessionmaker[Session],
) -> None:
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics engineer", "Data platform engineer")
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "analytics engineers", "BI developer", "Data platform engineer")
    found = row(factory, user_id)
    assert found is not None
    assert [one["title"] for one in found.suggestions] == [
        "Analytics engineer",
        "Data platform engineer",
        "BI developer",
    ]
    assert found.error is None
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics Engineer")
    found = row(factory, user_id)
    assert found is not None
    assert found.error == "nothing_new"
    assert len(found.suggestions) == 3
    with factory() as db:
        shown = titles.view(db, user_id, T0)
    assert shown["status"] == "idle"
    assert shown["error"] is None
    assert shown["note"] == titles.NOTES["nothing_new"]


def test_past_the_cap_the_oldest_ideas_go_and_the_newest_stay(
    factory: sessionmaker[Session],
) -> None:
    user_id = ready(factory)
    old = [{"title": f"Old role {n}", "reason": "Fits.", "language": "en"} for n in range(20)]
    with factory() as db:
        titles._row(db, user_id).suggestions = old
        db.commit()
    assert titles.LIST_MAX == 20
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "New role one", "New role two")
    found = row(factory, user_id)
    assert found is not None
    titles_kept = [one["title"] for one in found.suggestions]
    assert len(titles_kept) == titles.LIST_MAX
    assert titles_kept[:2] == ["Old role 2", "Old role 3"]
    assert titles_kept[-2:] == ["New role one", "New role two"]


def test_a_dismissed_idea_leaves_and_never_comes_back(factory: sessionmaker[Session]) -> None:
    user_id = ready(factory)
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics engineer", "BI developer")
    with factory() as db:
        assert titles.dismiss(db, user_id, " analytics  engineers ")
        assert not titles.dismiss(db, user_id, "BI developer - lead")
        assert not titles.dismiss(db, user_id, "")
        db.commit()
    found = row(factory, user_id)
    assert found is not None
    assert [one["title"] for one in found.suggestions] == ["BI developer"]
    assert found.dismissed == ["analytics engineers"]
    made = start(factory, user_id, T0, auto=False)
    with factory() as db:
        request = titles.run_request(db, made)
    assert request is not None
    assert request[1].dismissed == ("analytics engineers",)  # the agent is told
    end(factory, made, "Analytics Engineer", "Data platform engineer")
    found = row(factory, user_id)
    assert found is not None
    assert [one["title"] for one in found.suggestions] == ["BI developer", "Data platform engineer"]
    # A stored idea dismissed since is not shown either.
    with factory() as db:
        titles._row(db, user_id).suggestions = [
            *found.suggestions,
            {"title": "Analytics engineer", "reason": "Fits.", "language": "en"},
        ]
        db.commit()
    with factory() as db:
        shown = titles.view(db, user_id, T0)
    assert [one["title"] for one in shown["suggestions"]] == [
        "BI developer",
        "Data platform engineer",
    ]


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
    # The preferences (the wizard's dates, the documents' tone) are no trigger; any section the
    # agent reads is.
    save(factory, user_id, "preferences", {"documents_tone": "warm"}, T0 + timedelta(minutes=2))
    assert due(factory, T0 + timedelta(minutes=3)) == []
    for minute, (name, data) in enumerate(
        [
            ("search", SEARCH),
            ("identity", {"city": "Lille"}),
            ("facts", {"blocks": [{"title": "Goal", "body": "Product work."}]}),
            ("answers", {"notice_period": "One month"}),
            ("experience", {"employers": [], "education": []}),
        ]
    ):
        at = T0 + timedelta(minutes=10 * (minute + 1))
        save(factory, user_id, name, data, at)
        assert due(factory, at + timedelta(minutes=1)) == [user_id], name
        with factory() as db:
            titles.seen(db, user_id, at + timedelta(minutes=1))
            db.commit()


def test_an_upload_or_a_base_cv_makes_a_person_due_once_its_text_is_read(
    factory: sessionmaker[Session],
) -> None:
    from app.documents import store
    from app.documents.models import BaseSource
    from app.ingestion.models import IngestionDocument, TextSource, TextStatus

    user_id = ready(factory)
    with factory() as db:
        titles.seen(db, user_id, T0 + timedelta(minutes=1))
        document = IngestionDocument(
            user_id=user_id,
            name="cv.pdf",
            mime="application/pdf",
            size=10,
            sha256=b"c" * 32,
            text_source=TextSource.parsed,
            text_status=TextStatus.pending,
            parser_version=1,
            pending_since=T0 + timedelta(minutes=2),
            created_at=T0 + timedelta(minutes=2),
        )
        db.add(document)
        db.commit()
        document_id = document.id
    # Still being read: the refresh waits, so the run reads the text...
    assert due(factory, T0 + timedelta(minutes=3)) == []
    # ...and a document stuck past READ_WAIT holds nothing.
    assert due(factory, T0 + timedelta(minutes=2) + titles.READ_WAIT) == [user_id]
    with factory() as db:
        found = db.get(IngestionDocument, document_id)
        assert found is not None
        found.text, found.text_status = "Data engineer CV.", TextStatus.done
        found.pending_since = None
        db.commit()
    assert due(factory, T0 + timedelta(minutes=4)) == [user_id]
    with factory() as db:
        titles.seen(db, user_id, T0 + timedelta(minutes=5))
        db.commit()
    assert due(factory, T0 + timedelta(minutes=6)) == []
    with factory() as db:
        store.create_base(db, user_id, "Mine", BaseSource.built, at=T0 + timedelta(minutes=7))
        db.commit()
    assert due(factory, T0 + timedelta(minutes=8)) == [user_id]


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
    # A trigger, and nothing the agent reads: the name is left out.
    save(factory, unreadable, "identity", {"full_name": "Sam Example"})
    redis = FakeRedis()
    started = asyncio.run(agents.tick(factory, T0 + timedelta(minutes=1), redis))
    assert started == 1
    [run] = runs_of(factory, user_id)
    assert run.agent == titles.NAME
    assert run.status is RunStatus.queued
    # The budget is the ceiling of the estimate for what this run reads.
    with factory() as db:
        request = titles._request(db, user_id, db.get(TitleSuggestions, user_id))
    assert request is not None
    expected = titles.estimate(len(titles.prompt_for(request))).ceiling
    assert run.input["token_budget"] == expected
    assert 0 < expected < titles.TOKEN_BUDGET
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
    assert body["needs"] == "input"
    assert body["messages"] == []
    assert body["message_max"] == titles.MESSAGE_MAX
    assert body["note"] is None
    save(factory, user_id, "skills", SKILLS)
    body = api.get(URL, token=token).json()
    assert body["needs"] is None
    assert "asks_left" not in body
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
    assert response.json()["detail"] == (
        "Add your CV, fill your profile or tell the assistant what you want to do first."
    )
    save(factory, user_id, "skills", SKILLS)
    response = api.post(URL, token=token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert "asks_left" not in body
    [run] = runs_of(factory, user_id)
    assert recorded.jobs == [(agents.JOB, (str(run.id),), queues.AGENTS, agents.job_id(run.id))]
    again = api.post(URL, token=token)
    assert again.status_code == 409
    assert again.json()["detail"] == "Suggestions are already on their way."


def test_more_ideas_past_three_clicks_still_runs(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    # No daily limit (owner, 2026-11-06): a fourth click of the day runs like the first.
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "skills", SKILLS)
    for _ in range(4):
        assert api.post(URL, token=token).status_code == 200
        end(factory, _last_run(factory, user_id))


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
    assert "asks_left" not in body
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


def test_a_message_starts_a_run_counts_as_one_ask_and_can_be_cleared(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    too_long = api.post(URL, token=token, json={"message": "x" * (titles.MESSAGE_MAX + 1)})
    assert too_long.status_code == 422
    assert too_long.json()["detail"] == "Write at most 300 characters, in one short message."
    empty = api.post(URL, token=token, json={"message": "   "})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Write what you want to do first."
    assert runs_of(factory, user_id) == []
    # An empty profile: the message alone starts the run.
    response = api.post(URL, token=token, json={"message": " Move into\nproduct "})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert "asks_left" not in body
    assert [one["text"] for one in body["messages"]] == ["Move into product"]
    assert len(recorded.jobs) == 1
    end(factory, _last_run(factory, user_id), "Product analyst")
    cleared = api.request("DELETE", f"{URL}/messages", token=token)
    assert cleared.status_code == 200
    assert cleared.json()["messages"] == []
    assert row(factory, user_id).messages == []  # type: ignore[union-attr]
    assert cleared.json()["needs"] == "input"  # nothing left to read
    assert api.request("DELETE", f"{URL}/messages").status_code == 401


def test_not_for_me_removes_an_idea_for_good_and_refuses_what_is_not_a_title(
    factory: sessionmaker[Session], api: Api, recorded: RecordingQueue
) -> None:
    user_id, token = signed(factory, api)
    give_key(factory, user_id)
    save(factory, user_id, "skills", SKILLS)
    made = start(factory, user_id, T0, auto=False)
    end(factory, made, "Analytics engineer", "BI developer")
    response = api.post(f"{URL}/dismiss", token=token, json={"title": "Analytics engineer"})
    assert response.status_code == 200, response.text
    assert [one["title"] for one in response.json()["suggestions"]] == ["BI developer"]
    bad = api.post(f"{URL}/dismiss", token=token, json={"title": "BI developer, Paris"})
    assert bad.status_code == 422
    assert bad.json()["detail"] == "This is not a job title the suggestions hold."
    # Another person's row is never touched.
    _, other = signed(factory, api, OTHER)
    api.post(f"{URL}/dismiss", token=other, json={"title": "BI developer"})
    assert [one["title"] for one in api.get(URL, token=token).json()["suggestions"]] == [
        "BI developer"
    ]
    assert api.post(f"{URL}/dismiss", json={"title": "BI developer"}).status_code == 401


def test_a_whole_run_reads_the_documents_and_the_message(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    from app.ingestion import estimate_router
    from app.ingestion.models import IngestionConsent, IngestionDocument, TextSource, TextStatus

    runtime = Runtime(factory, migrated_url)
    user_id = person(factory)
    give_key(factory, user_id, keyring=runtime.keyring)
    with factory() as db:
        db.add(IngestionConsent(user_id=user_id, text_version=estimate_router.CONSENT_VERSION))
        db.add(
            IngestionDocument(
                user_id=user_id,
                name="cv.txt",
                mime="text/plain",
                size=40,
                sha256=b"d" * 32,
                text="Data engineer at Example Robotics: SQL, dbt and Python.",
                text_source=TextSource.pasted,
                text_status=TextStatus.done,
                parser_version=1,
            )
        )
        db.commit()
    prompts: list[str] = []
    original = titles.prompt_for

    def spy(request: titles.Request) -> str:
        prompts.append(original(request))
        return prompts[-1]

    titles.prompt_for = spy  # type: ignore[assignment]
    try:
        made = start(factory, user_id, T0, auto=False)
        assert isinstance(made, uuid.UUID)
        with factory() as db:
            titles._row(db, user_id).messages = [{"text": "Stay in data", "at": T0.isoformat()}]
            db.commit()
        prompts.clear()
        asyncio.run(agents.execute(runtime.context(), made))
    finally:
        titles.prompt_for = original  # type: ignore[assignment]
    sent = [one for one in prompts if "Example Robotics" in one]
    assert sent, "the backend's prompt holds the document's text"
    assert '"Stay in data"' in sent[0]
    found = row(factory, user_id)
    assert found is not None
    assert [one["title"] for one in found.suggestions][:2] == [
        "Analytics engineer",
        "Data platform engineer",
    ]
