"""D29: the documents tick once nothing is written without a click, on a real Postgres at head, two
e2e people. No test sleeps or reads the clock: every pass gets its `at`.

Nothing writes without a click: a found posting at fit 95 gets nothing, with or without a base CV. A
generation of trigger `auto` left open from D10 is cancelled once, its document and versions
untouched. What the tick still does: the clicks that waited for a credential or a base CV start, and
one stored waiting for the documents consent removed by D30 starts at once; a click with no base CV
builds one from the profile and the CV waits for it, then is written from it; a run that ended
without its final hook is settled after the grace; a held person is left to the next pass; one
person's failure never ends the pass; the cron queues runs by id."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, failures, runner, runs
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind
from app.documents import (  # noqa: F401 (writers registers the writers)
    auto_router,
    estimate,
    generate,
    render_job,
    store,
    tick,
    writers,
)
from app.documents.models import (
    Author,
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
)
from app.jobs.models import Job
from tests.agents.conftest import API_KEY, new_key
from tests.conftest import make_settings
from tests.documents.canary import marker
from tests.documents.conftest import load
from tests.documents.render_support import CV, give_profile
from tests.documents.store_support import (
    AT,
    USER_A,
    USER_B,
    add_user,
    engine,
    factory,
    migrated_url,
    short_locks,
)

__all__ = ["engine", "factory", "migrated_url", "short_locks"]

TODAY = AT.date()
POSTING = load("posting_lumen.json")["text"]


class World:
    """One person: profile, credential and base CV as asked; postings added with their fit."""

    def __init__(self, factory: sessionmaker[Session], url: str, email: str, **flags: Any):
        key = new_key()
        self.factory = factory
        self.settings = make_settings(url, credentials_key=key, agents_fake_backend=True)
        self.keyring = Keyring.from_text(key)
        with factory() as db:
            self.user = add_user(db, email)
            db.commit()
        if flags.get("profile", True):
            give_profile(factory, self.user, AT)
        else:
            with factory() as db:
                from app.profile import service as profile_service

                profile_service.get_profile(db, self.user, AT)
                db.commit()
        if flags.get("credential", True):
            self.credential()
        if flags.get("base", True):
            with factory() as db:
                made = store.create_base(
                    db,
                    self.user,
                    "My CV",
                    BaseSource.profile,
                    at=AT,
                    content=load("base_cv_alex.json"),
                )
                db.commit()
                self.base = made.id

    def credential(self) -> None:
        with self.factory() as db:
            credentials.save(
                db, self.keyring, self.user, CredentialKind.anthropic_api_key, API_KEY, AT
            )
            db.commit()

    def search(self, **documents: Any) -> None:
        """Store a search section with this `documents` block (and its other keys at rest)."""
        extra = documents.pop("extra", {})
        with self.factory() as db:
            row = db.execute(
                sa.text("SELECT sections FROM profiles WHERE user_id = :u"), {"u": self.user}
            ).scalar_one()
            search = dict(row.get("search") or {})
            search.update(extra)
            search["documents"] = {**search.get("documents", {}), **documents}
            row["search"] = search
            db.execute(
                sa.text("UPDATE profiles SET sections = CAST(:s AS jsonb) WHERE user_id = :u"),
                {"s": json.dumps(row), "u": self.user},
            )
            db.commit()

    def posting(
        self,
        fit: int | None,
        *,
        found_on: date = TODAY,
        status: str = "found",
        title: str = "Platform engineer",
        source: str | None = None,
        text: str = POSTING,
    ) -> uuid.UUID:
        with self.factory() as db:
            made = db.execute(
                sa.text(
                    "INSERT INTO jobs (user_id, slug, company, title, posting_text, fit, "
                    "found_on, status, source) VALUES (:u, :s, 'Example Corp', :ti, :t, :f, :d, "
                    "CAST(:st AS job_status), :so) RETURNING id"
                ),
                {
                    "u": self.user,
                    "s": f"example-{uuid.uuid4().hex[:8]}",
                    "ti": title,
                    "t": text,
                    "f": fit,
                    "d": found_on,
                    "st": status,
                    "so": source,
                },
            ).scalar_one()
            db.commit()
            return made

    def tick(self, at: datetime = AT) -> list[uuid.UUID]:
        return tick.tick(self.factory, at, self.settings, only=[self.user])

    def generations(self, posting: uuid.UUID | None = None) -> list[dict[str, Any]]:
        with self.factory() as db:
            query = (
                sa.select(
                    DocumentGeneration.id,
                    DocumentGeneration.mode,
                    DocumentGeneration.trigger,
                    DocumentGeneration.status,
                    DocumentGeneration.wait_reason,
                    DocumentGeneration.document_id,
                    DocumentGeneration.ceiling_day,
                    DocumentGeneration.token_ceiling,
                    DocumentGeneration.base_document_id,
                    DocumentGeneration.run_id,
                    Document.kind,
                    Document.posting_id,
                    Document.scope,
                )
                .join(Document, Document.id == DocumentGeneration.document_id)
                .where(DocumentGeneration.user_id == self.user)
                .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
            )
            if posting is not None:
                query = query.where(Document.posting_id == posting)
            return [dict(row._mapping) for row in db.execute(query)]

    def finish(
        self,
        status: str = "done",
        error: str | None = None,
        at: datetime = AT,
        *,
        counted: bool = True,
    ) -> None:
        """Every running generation of the person ends with `status` (as the writer's hook), and
        its run with the token counts the runner writes (none when not `counted`)."""
        with self.factory() as db:
            db.execute(
                sa.text(
                    "UPDATE document_generations SET status = CAST(:s AS generation_status), "
                    "error = :e, finished_at = :at WHERE user_id = :u AND status = 'running'"
                ),
                {"s": status, "e": error, "u": self.user, "at": at},
            )
            counts = (
                ", input_tokens = coalesce(input_tokens, 0), "
                "output_tokens = coalesce(output_tokens, 0)"
                if counted
                else ""
            )
            db.execute(
                sa.text(
                    f"UPDATE agent_runs SET status = 'done'{counts} WHERE user_id = :u "
                    "AND status IN ('queued', 'running')"
                ),
                {"u": self.user},
            )
            db.commit()

    def set_job(self, posting: uuid.UUID, **values: Any) -> None:
        with self.factory() as db:
            db.execute(sa.update(Job).where(Job.id == posting).values(**values))
            db.commit()


@pytest.fixture
def world(factory: sessionmaker[Session], migrated_url: str) -> World:
    return World(factory, migrated_url, USER_A)


def posting_cv(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [g for g in found if g["mode"] is GenerationMode.posting and g["kind"] is Kind.cv]


# --- the first base CV --------------------------------------------------------------------------


def click(who: World, posting: uuid.UUID, *, start: bool = True) -> uuid.UUID:
    """A "Write" of the posting's CV, as `auto_router.write_now` does it, in its own session."""
    from app.documents import writer_base

    with who.factory() as db:
        store.lock_person(db, who.user)
        store.lock_posting(db, who.user, posting)
        reason = writer_base.click_wait(db, who.user, AT)
        document = store.get_or_create(db, who.user, posting, Kind.cv, at=AT)
        auto_router._give_way(db, document, AT)
        figures = estimate.for_posting(db, who.user, posting, None)
        made = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            None,
            estimate.as_json(figures),
            figures.ceiling,
            at=AT,
            status=GenerationStatus.waiting if reason else GenerationStatus.queued,
            wait_reason=reason,
        )
        if start and reason is None:
            generate.start(db, made, at=AT, settings=who.settings)
        store.commit(db)
        return made.id


def end_runs(who: World, status: str, error: str | None, finished: datetime | None) -> None:
    """The person's runs end as the reaper, a killed worker or a cancel leave them: no hook."""
    with who.factory() as db:
        db.execute(
            sa.text(
                "UPDATE agent_runs SET status = CAST(:s AS agent_run_status), error = :e, "
                "finished_at = :f WHERE user_id = :u"
            ),
            {"s": status, "e": error, "f": finished, "u": who.user},
        )
        db.commit()


# --- nothing without a click --------------------------------------------------------------------


@pytest.mark.parametrize("base", [True, False])
def test_a_found_posting_at_fit_95_gets_nothing_without_a_click(
    factory: sessionmaker[Session], migrated_url: str, base: bool
) -> None:
    who = World(factory, migrated_url, USER_A, base=base)
    who.posting(95)
    who.search(auto=True, min_fit=0, daily_ceiling=500)  # an old profile's block, unread
    assert who.tick() == []
    assert who.tick(AT + timedelta(days=1)) == []
    assert who.generations() == []
    with who.factory() as db:
        assert len(store.base_cvs(db, who.user)) == (1 if base else 0)


def test_an_open_automatic_generation_is_cancelled_once_and_its_document_kept(
    world: World,
) -> None:
    posting = world.posting(90)
    with world.factory() as db:
        document = store.get_or_create(db, world.user, posting, Kind.cv, at=AT)
        store.save_draft(
            db, world.user, document.id, CV, document.draft_revision, Author.user, at=AT
        )
        generate.create(
            db, document, GenerationMode.posting, GenerationTrigger.auto, None, {}, 1000, at=AT
        )
        store.commit(db)
        draft = store.get(db, world.user, document.id)
        assert draft is not None
        revision = draft.draft_revision
    assert world.tick() == []
    [found] = world.generations(posting)
    assert (found["trigger"], found["status"]) == (
        GenerationTrigger.auto,
        GenerationStatus.cancelled,
    )
    assert world.tick() == []
    assert len(world.generations(posting)) == 1
    with world.factory() as db:
        kept = store.get(db, world.user, document.id)
        assert kept is not None
        assert kept.draft_revision == revision


# --- the clicks that waited ---------------------------------------------------------------------


def test_a_click_with_a_credential_and_no_consent_row_waits_for_nothing(world: World) -> None:
    posting = world.posting(40)
    click(world, posting)
    with world.factory() as db:
        assert db.scalar(sa.text("SELECT count(*) FROM document_consents")) == 0
    [found] = world.generations(posting)
    assert (found["status"], found["wait_reason"]) == (GenerationStatus.queued, None)


def test_a_click_stored_waiting_for_the_documents_consent_starts_at_the_next_tick(
    world: World,
) -> None:
    """D30: a generation left waiting with the reason `consent` before the deploy is written."""
    posting = world.posting(40)
    generation_id = click(world, posting)
    with world.factory() as db:
        db.execute(
            sa.update(DocumentGeneration)
            .where(DocumentGeneration.id == generation_id)
            .values(status=GenerationStatus.waiting, wait_reason=GenerationWait.consent)
        )
        db.commit()
    started = world.tick()
    [running] = world.generations(posting)
    assert (running["status"], running["wait_reason"]) == (GenerationStatus.running, None)
    assert started == [running["run_id"]]


def test_a_click_with_no_base_cv_builds_it_first_then_the_cv_from_it(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False)
    posting = who.posting(55)
    click(who, posting)
    found = who.generations()
    base = [g for g in found if g["mode"] is GenerationMode.base_profile]
    [cv] = posting_cv(found)
    assert [(g["trigger"], g["status"]) for g in base] == [
        (GenerationTrigger.click, GenerationStatus.queued)
    ]
    assert (cv["status"], cv["wait_reason"]) == (GenerationStatus.waiting, GenerationWait.base_cv)
    first = who.tick()  # the base CV starts, the CV still waits for it
    [base_run] = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert first == [base_run["run_id"]]
    assert posting_cv(who.generations())[0]["wait_reason"] is GenerationWait.base_cv
    who.finish()  # the base CV is written
    second = who.tick(AT + timedelta(seconds=1))
    [cv] = posting_cv(who.generations())
    assert cv["status"] is GenerationStatus.running
    assert second == [cv["run_id"]]
    with who.factory() as db:
        default = store.default_base(db, who.user)
        assert default is not None
        assert cv["base_document_id"] == default.id


def _auto_base_build(
    who: World, status: GenerationStatus, reason: GenerationWait | None = None
) -> uuid.UUID:
    """The first base CV D10 built at the tick, left open at the deploy of D29: the skeleton
    "My CV" as the default and its generation of trigger `auto`, not started."""
    from app.documents import writer_base

    with who.factory() as db:
        built = writer_base.build_from_profile(
            db, who.user, at=AT, source=BaseSource.profile, trigger=GenerationTrigger.auto
        )
        assert built.generation is not None
        built.generation.status = status
        built.generation.wait_reason = reason
        store.commit(db)
        return built.document.id


@pytest.mark.parametrize("stored_waiting", [False, True])
def test_an_automatic_base_cv_build_is_cancelled_never_started_by_the_tick(
    factory: sessionmaker[Session], migrated_url: str, stored_waiting: bool
) -> None:
    """Review of D29, item 2 (a): a queued one, or one stored waiting for the consent."""
    who = World(factory, migrated_url, USER_A, base=False)
    document = (
        _auto_base_build(who, GenerationStatus.waiting, GenerationWait.consent)
        if stored_waiting
        else _auto_base_build(who, GenerationStatus.queued)
    )
    assert who.tick() == []
    [found] = who.generations()
    assert (found["document_id"], found["trigger"], found["status"], found["run_id"]) == (
        document,
        GenerationTrigger.auto,
        GenerationStatus.cancelled,
        None,
    )
    assert who.tick() == []


def test_a_click_after_a_cancelled_automatic_base_cv_builds_that_base_cv_first(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    """Review of D29, item 2 (b): the never written skeleton is no base CV to write from."""
    who = World(factory, migrated_url, USER_A, base=False)
    document = _auto_base_build(who, GenerationStatus.queued)
    assert who.tick() == []  # the automatic build is cancelled
    posting = who.posting(55)
    click(who, posting)
    found = who.generations()
    base = [g for g in found if g["mode"] is GenerationMode.base_profile]
    # Both were made at the test's one fixed time: their order is the database's to choose.
    made = sorted(((g["document_id"], g["trigger"], g["status"]) for g in base), key=str)
    assert made == sorted(
        [
            (document, GenerationTrigger.auto, GenerationStatus.cancelled),
            (document, GenerationTrigger.click, GenerationStatus.queued),
        ],
        key=str,
    )
    [cv] = posting_cv(found)
    assert (cv["status"], cv["wait_reason"]) == (GenerationStatus.waiting, GenerationWait.base_cv)
    with who.factory() as db:
        assert [b.id for b in store.base_cvs(db, who.user)] == [document]
    first = who.tick()  # the base CV starts first, the CV waits for it
    assert first == [
        g["run_id"] for g in who.generations() if g["status"] is GenerationStatus.running
    ]
    assert posting_cv(who.generations())[0]["wait_reason"] is GenerationWait.base_cv


def test_two_clicks_at_once_build_one_base_cv(
    engine: sa.Engine, factory: sessionmaker[Session], migrated_url: str
) -> None:
    """Review of D29, item 7: the second click waits for lock 1 and reads the first one's base
    CV again under it, instead of building a second "My CV"."""
    import threading

    from app.documents import writer_base
    from tests.documents.test_store import _wait_until_blocked

    who = World(factory, migrated_url, USER_A, base=False)
    outcome: dict[str, Any] = {}
    with factory() as first, factory() as second:
        assert writer_base.click_wait(first, who.user, AT) is GenerationWait.base_cv
        pid = second.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()

        def other_click() -> None:
            try:
                outcome["reason"] = writer_base.click_wait(second, who.user, AT)
                store.commit(second)
            except Exception as exc:  # the main thread asserts on it
                outcome["error"] = exc
                second.rollback()

        thread = threading.Thread(target=other_click)
        thread.start()
        _wait_until_blocked(engine, pid, thread)
        assert thread.is_alive()  # it waits for lock 1, before it reads the base CVs
        store.commit(first)
        thread.join(20)
    assert "error" not in outcome
    assert outcome["reason"] is GenerationWait.base_cv
    with factory() as db:
        assert len(store.base_cvs(db, who.user)) == 1
    builds = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert len(builds) == 1


def test_a_click_meeting_the_limit_of_active_runs_stays_queued_for_the_next_tick(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    posting = world.posting(60)
    click(world, posting, start=False)

    def busy(*_: Any, **__: Any) -> Any:
        raise runs.ActiveRunsError("busy")

    monkeypatch.setattr(tick.generate, "start", busy)
    assert world.tick() == []
    assert posting_cv(world.generations(posting))[0]["status"] is GenerationStatus.queued
    monkeypatch.undo()
    assert len(world.tick()) == 1


# --- recovery, the lock, one person's failure, the cron -----------------------------------------


def test_the_grace_is_over_the_runners_final_hook_timeout() -> None:
    assert runner.FINAL_HOOK_TIMEOUT_S < tick.HOOK_GRACE_S


@pytest.mark.parametrize(
    ("run", "error", "status", "code"),
    [
        ("failed", failures.TOKEN_LIMIT, GenerationStatus.failed, "token_limit"),
        ("failed", "The worker stopped.", GenerationStatus.failed, "run_failed"),
        ("done", None, GenerationStatus.failed, "run_failed"),
        ("cancelled", None, GenerationStatus.cancelled, None),
        ("running", None, GenerationStatus.running, None),
    ],
)
def test_a_clicks_run_that_ended_without_its_hook_is_settled_after_the_grace(
    world: World, run: str, error: str | None, status: GenerationStatus, code: str | None
) -> None:
    posting = world.posting(90)
    click(world, posting)
    end_runs(world, run, error, AT if run != "running" else None)
    world.tick(AT + timedelta(seconds=tick.HOOK_GRACE_S - 1))
    assert posting_cv(world.generations(posting))[0]["status"] is GenerationStatus.running
    world.tick(AT + timedelta(seconds=tick.HOOK_GRACE_S + 1))
    [found] = posting_cv(world.generations(posting))
    with world.factory() as db:
        row = db.get(DocumentGeneration, found["id"])
        assert row is not None
        assert (row.status, row.error) == (status, code)


def test_a_pass_that_finds_the_person_held_does_nothing(world: World) -> None:
    posting = world.posting(80)
    click(world, posting, start=False)
    with world.factory() as holder:
        assert tick.try_lock(holder, world.user)
        assert world.tick() == []
    assert len(world.tick()) == 1


def test_an_unexpected_error_for_one_person_never_ends_the_pass_for_the_next(
    factory: sessionmaker[Session],
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alex = World(factory, migrated_url, USER_A)
    blake = World(factory, migrated_url, USER_B)
    click(alex, alex.posting(90), start=False)
    blake_posting = blake.posting(80)
    click(blake, blake_posting, start=False)
    planted = marker()
    real = tick.start_clicks

    def broken(db: Session, user_id: uuid.UUID, *args: Any) -> list[uuid.UUID]:
        if user_id == alex.user:
            raise RuntimeError(planted)
        return real(db, user_id, *args)

    monkeypatch.setattr(tick, "start_clicks", broken)
    with caplog.at_level(logging.INFO, logger="app.documents.tick"):
        started = tick.tick(factory, AT, alex.settings, only=[alex.user, blake.user])
    assert [g["run_id"] for g in posting_cv(blake.generations(blake_posting))] == started
    failed = [r for r in caplog.records if r.getMessage() == "documents.tick.person_failed"]
    assert [(r.user_id, r.error) for r in failed] == [(str(alex.user), "RuntimeError")]  # type: ignore[attr-defined]
    assert planted not in caplog.text


def test_the_worker_runs_the_tick_every_minute_on_the_documents_queue() -> None:
    from app import worker

    spec = worker.documents_queue()
    assert "documents_tick" in [found.name for found in spec.functions]
    crons = {found.name: found for found in spec.cron_jobs}
    assert crons["cron:documents_tick"].second == worker.DOCUMENTS_TICK_SECOND


def test_the_cron_job_takes_ids_only_and_queues_the_runs(world: World) -> None:
    import asyncio

    click(world, world.posting(80), start=False)
    sent: list[tuple[str, tuple[Any, ...]]] = []

    class Pool:
        async def enqueue_job(self, function: str, *args: Any, **_: Any) -> object:
            sent.append((function, args))
            return object()

    ctx = {
        "sessions": world.factory,
        "clock": lambda: AT,
        "settings": world.settings,
        "redis": Pool(),
    }
    assert asyncio.run(tick.run(ctx)) == 1
    assert [function for function, _ in sent] == ["run_agent"]
    assert all(isinstance(arg, str) and uuid.UUID(arg) for _, args in sent for arg in args)
