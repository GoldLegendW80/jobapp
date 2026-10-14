"""D10: the tick on a real Postgres at head, two e2e people, the fake backend for the one test
that runs the writers end to end. No test sleeps or reads the clock: every pass gets its `at`.

The first base CV built from the profile (and nothing from an empty profile); postings inserted
by a sweep and by hand both picked up; the threshold at, under, without a fit, lowered (the last
30 days now written, highest fit first) and raised (nothing removed); 31 days old; a never apply
rule; `auto` off; the consent and the credential (waiting, nothing counted, then started); the
safety ceiling in America/New_York while the server is in UTC; one automatic run at a time; a
posting skipped or fallen under the threshold while waiting (cancelled); the retry once; the
default base CV changed; legacy files; the tick lock held by another pass; and the canary."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind
from app.documents import consent, store, tick, writers  # noqa: F401 (registers the writers)
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Scope,
)
from app.jobs.models import Job
from tests.agents.conftest import API_KEY, new_key
from tests.conftest import make_settings
from tests.documents.canary import find, marker
from tests.documents.conftest import load
from tests.documents.render_support import give_profile
from tests.documents.store_support import (
    AT,
    USER_A,
    USER_B,
    add_job_file,
    add_user,
    engine,
    factory,
    migrated_url,
)
from tests.documents.writer_support import Person, execute

__all__ = ["engine", "factory", "migrated_url"]

TODAY = AT.date()
POSTING = load("posting_lumen.json")["text"]


class World:
    """One person: profile, consent, credential as asked; postings added with their fit."""

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
        if flags.get("consent", True):
            self.consent()
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

    def consent(self) -> None:
        with self.factory() as db:
            consent.record(db, self.user, AT)
            db.commit()

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

    def finish(self, status: str = "done", error: str | None = None, at: datetime = AT) -> None:
        """Every running generation of the person ends with `status` (as the writer's hook)."""
        with self.factory() as db:
            db.execute(
                sa.text(
                    "UPDATE document_generations SET status = CAST(:s AS generation_status), "
                    "error = :e, finished_at = :at WHERE user_id = :u AND status = 'running'"
                ),
                {"s": status, "e": error, "u": self.user, "at": at},
            )
            db.execute(
                sa.text(
                    "UPDATE agent_runs SET status = 'done' WHERE user_id = :u "
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


def test_the_first_base_cv_is_built_from_the_profile_and_postings_wait_for_it(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False)
    posting = who.posting(80)
    runs = who.tick()
    found = who.generations()
    base = [g for g in found if g["mode"] is GenerationMode.base_profile]
    assert len(base) == 1
    assert base[0]["trigger"] is GenerationTrigger.auto
    assert base[0]["status"] is GenerationStatus.running
    assert runs == [base[0]["run_id"]]
    with factory() as db:
        built = store.base_cvs(db, who.user)
        assert [(b.base_source, b.is_default) for b in built] == [(BaseSource.profile, True)]
    cv = posting_cv(who.generations(posting))
    assert [(g["status"], g["wait_reason"]) for g in cv] == [
        (GenerationStatus.waiting, GenerationWait.base_cv)
    ]
    # A second pass builds no second base CV.
    assert who.tick() == []
    assert len([g for g in who.generations() if g["mode"] is GenerationMode.base_profile]) == 1


def test_an_empty_profile_without_a_base_cv_gets_nothing(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False, profile=False)
    who.posting(90)
    assert who.tick() == []
    assert who.generations() == []
    with factory() as db:
        assert store.base_cvs(db, who.user) == []
        assert db.scalar(sa.select(sa.func.count()).select_from(Document)) == 0


# --- which postings -----------------------------------------------------------------------------


def test_postings_from_a_sweep_and_by_hand_are_both_picked_up_at_the_threshold_only(
    world: World,
) -> None:
    swept = world.posting(80, source="ashby")
    by_hand = world.posting(70)
    under = world.posting(69)
    no_fit = world.posting(None)
    runs = world.tick()
    assert len(runs) == 1  # one automatic run at a time
    states = {
        p: [(g["status"], g["wait_reason"]) for g in posting_cv(world.generations(p))]
        for p in (swept, by_hand, under, no_fit)
    }
    assert states[swept] == [(GenerationStatus.running, None)]  # highest fit first
    assert states[by_hand] == [(GenerationStatus.queued, None)]
    assert states[under] == []
    assert states[no_fit] == []
    world.finish()
    assert len(world.tick()) == 1  # the letter of the written CV first: the pair is finished
    letters = [g for g in world.generations(swept) if g["kind"] is Kind.letter]
    assert [g["status"] for g in letters] == [GenerationStatus.running]
    world.finish()
    assert len(world.tick()) == 1
    assert [g["status"] for g in posting_cv(world.generations(by_hand))] == [
        GenerationStatus.running
    ]


def test_a_posting_found_31_or_30_days_ago_is_not_written_29_is(world: World) -> None:
    old = world.posting(90, found_on=TODAY - timedelta(days=31))
    edge = world.posting(90, found_on=TODAY - timedelta(days=30))
    inside = world.posting(90, found_on=TODAY - timedelta(days=29))
    world.tick()
    assert posting_cv(world.generations(old)) == []
    assert posting_cv(world.generations(edge)) == []
    assert len(posting_cv(world.generations(inside))) == 1


def test_lowering_the_threshold_writes_the_last_30_days_highest_first_and_raising_removes_nothing(
    world: World,
) -> None:
    sixty = world.posting(60)
    fifty_five = world.posting(55)
    forty = world.posting(40)
    world.tick()
    assert world.generations() == []
    world.search(min_fit=50)
    world.tick()
    assert [g["status"] for g in posting_cv(world.generations(sixty))] == [GenerationStatus.running]
    assert [g["status"] for g in posting_cv(world.generations(fifty_five))] == [
        GenerationStatus.queued
    ]
    assert posting_cv(world.generations(forty)) == []
    world.finish()
    world.search(min_fit=90)
    world.tick()
    # the written one stays written; the queued one under the new threshold is cancelled
    assert [g["status"] for g in posting_cv(world.generations(sixty))] == [GenerationStatus.done]
    assert [g["status"] for g in posting_cv(world.generations(fifty_five))] == [
        GenerationStatus.cancelled
    ]


def test_a_never_apply_rule_excludes_a_posting_added_by_hand(world: World) -> None:
    world.search(extra={"never_apply": ["No security roles: security"]})
    excluded = world.posting(95, title="Security engineer")
    kept = world.posting(80)
    world.tick()
    assert posting_cv(world.generations(excluded)) == []
    assert len(posting_cv(world.generations(kept))) == 1


def test_auto_off_creates_nothing_and_cancels_what_waits(world: World) -> None:
    first = world.posting(90)
    second = world.posting(85)
    world.tick()
    world.search(auto=False)
    world.tick()
    assert [g["status"] for g in posting_cv(world.generations(first))] == [GenerationStatus.running]
    assert [g["status"] for g in posting_cv(world.generations(second))] == [
        GenerationStatus.cancelled
    ]
    third = world.posting(99)
    world.tick()
    assert posting_cv(world.generations(third)) == []


def test_imported_postings_with_legacy_files_are_never_written(world: World) -> None:
    imported = world.posting(95, status="found")
    with world.factory() as db:
        add_job_file(db, imported)
        db.commit()
    world.tick()
    assert world.generations(imported) == []


# --- waiting, never failing ---------------------------------------------------------------------


def test_without_consent_or_credential_nothing_starts_and_nothing_counts(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, consent=False, credential=False)
    posting = who.posting(80)
    assert who.tick() == []
    assert [(g["status"], g["wait_reason"]) for g in who.generations(posting)] == [
        (GenerationStatus.waiting, GenerationWait.consent)
    ]
    who.consent()
    assert who.tick() == []
    assert [(g["status"], g["wait_reason"], g["ceiling_day"]) for g in who.generations()] == [
        (GenerationStatus.waiting, GenerationWait.credential, None)
    ]
    with factory() as db:
        assert tick.started_today(db, who.user, TODAY) == 0
    who.credential()
    assert len(who.tick()) == 1
    assert [(g["status"], g["ceiling_day"]) for g in who.generations()] == [
        (GenerationStatus.running, TODAY)
    ]


def test_a_posting_skipped_or_under_the_threshold_while_waiting_is_cancelled(
    world: World,
) -> None:
    running = world.posting(95)
    skipped = world.posting(90)
    dropped = world.posting(85)
    world.tick()
    world.set_job(skipped, status="skipped")
    world.set_job(dropped, fit=60)
    world.tick()
    assert [g["status"] for g in world.generations(skipped)] == [GenerationStatus.cancelled]
    assert [g["status"] for g in world.generations(dropped)] == [GenerationStatus.cancelled]
    assert [g["status"] for g in world.generations(running)] == [GenerationStatus.running]


# --- the safety ceiling -------------------------------------------------------------------------


def test_the_ceiling_waits_until_midnight_in_new_york_while_the_server_is_in_utc(
    world: World,
) -> None:
    world.search(daily_ceiling=1, extra={"sweep_schedule": {"timezone": "America/New_York"}})
    first = world.posting(90)
    second = world.posting(85)
    morning = datetime(2026, 10, 14, 13, 0, tzinfo=AT.tzinfo)  # 09:00 in New York
    world.tick(morning)
    world.finish(at=morning)
    late = datetime(2026, 10, 15, 3, 59, tzinfo=AT.tzinfo)  # 23:59 on the 14th in New York
    assert len(world.tick(late)) == 1  # the letter never waits: its posting counted with its CV
    assert [g["kind"] for g in world.generations(first)] == [Kind.cv, Kind.letter]
    world.finish(at=late)
    assert world.tick(late) == []
    assert [(g["status"], g["wait_reason"]) for g in posting_cv(world.generations(second))] == [
        (GenerationStatus.waiting, GenerationWait.daily_ceiling)
    ]
    midnight = datetime(2026, 10, 15, 4, 0, tzinfo=AT.tzinfo)  # 00:00 on the 15th in New York
    assert len(world.tick(midnight)) == 1
    found = posting_cv(world.generations(second))
    assert [(g["status"], g["ceiling_day"]) for g in found] == [
        (GenerationStatus.running, date(2026, 10, 15))
    ]
    assert [g["ceiling_day"] for g in posting_cv(world.generations(first))] == [date(2026, 10, 14)]


def test_the_first_base_cv_with_eleven_postings_writes_all_highest_fit_first(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False)
    fits = [71, 99, 80, 75, 93, 88, 70, 72, 95, 84, 90]
    ids = {who.posting(fit, found_on=TODAY - timedelta(days=n)): fit for n, fit in enumerate(fits)}
    stale = who.posting(99, found_on=TODAY - timedelta(days=31))
    who.tick()
    who.finish()  # the base CV is written
    order: list[int] = []
    for _ in range(2 * len(fits) + 1):  # a CV, then its letter, for each
        who.tick()
        for g in who.generations():
            if g["status"] is GenerationStatus.running and g["kind"] is Kind.cv:
                order.append(ids[g["posting_id"]])
        who.finish()
    assert order == sorted(fits, reverse=True)
    assert who.generations(stale) == []


# --- retries, base CVs, locks -------------------------------------------------------------------


def test_a_failed_generation_is_retried_once_after_ten_minutes(world: World) -> None:
    posting = world.posting(80)
    world.tick()
    world.finish("failed", "run_failed")
    assert world.tick(AT + timedelta(seconds=599)) == []
    assert len(world.tick(AT + timedelta(seconds=600))) == 1
    world.finish("failed", "run_failed", at=AT + timedelta(seconds=600))
    assert world.tick(AT + timedelta(days=1)) == []
    assert [g["status"] for g in world.generations(posting)] == [GenerationStatus.failed] * 2


def test_a_stop_at_the_ceiling_is_retried_at_once_with_one_more_high_estimate(
    world: World,
) -> None:
    posting = world.posting(80)
    world.tick()
    world.finish("failed", "token_limit")
    assert len(world.tick()) == 1
    first, second = world.generations(posting)
    with world.factory() as db:
        high = db.get(DocumentGeneration, second["id"]).estimate["high"]  # type: ignore[union-attr]
    assert second["token_ceiling"] == first["token_ceiling"] + high


def test_a_changed_default_base_cv_is_used_by_later_postings_only(world: World) -> None:
    earlier = world.posting(90)
    world.tick()
    with world.factory() as db:
        other = store.create_base(db, world.user, "Other", BaseSource.built, at=AT)
        store.set_default(db, world.user, other.id, AT)
        db.commit()
        other_id = other.id
    later = world.posting(85)
    world.finish()
    world.tick()
    assert [g["base_document_id"] for g in posting_cv(world.generations(earlier))] == [world.base]
    assert [g["base_document_id"] for g in posting_cv(world.generations(later))] == [other_id]


def test_a_pass_that_finds_the_person_held_does_nothing(world: World) -> None:
    world.posting(80)
    with world.factory() as holder:
        assert tick.try_lock(holder, world.user)
        assert world.tick() == []
        assert world.generations() == []
    assert len(world.tick()) == 1


def test_two_passes_in_a_row_start_one_run(world: World) -> None:
    world.posting(80)
    world.posting(81)
    first = world.tick()
    second = world.tick()
    assert len(first) == 1
    assert second == []
    running = [g for g in world.generations() if g["status"] is GenerationStatus.running]
    assert len(running) == 1


def test_another_persons_rows_are_never_touched(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    alex = World(factory, migrated_url, USER_A)
    blake = World(factory, migrated_url, USER_B, consent=False)
    alex.posting(80)
    blake_posting = blake.posting(90)
    alex.tick()
    assert blake.generations() == []
    blake.tick()
    assert [g["wait_reason"] for g in blake.generations(blake_posting)] == [GenerationWait.consent]
    assert all(g["posting_id"] != blake_posting for g in alex.generations())


# --- end to end on the fake backend -------------------------------------------------------------


def test_a_found_posting_gets_its_cv_then_its_letter_and_no_text_reaches_the_logs(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    posting = world.posting(80, text=POSTING + " " + planted)
    caplog.set_level(logging.DEBUG)
    who = Person(world.factory, world.settings, world.keyring, world.user, posting, world.base)
    (cv_run,) = world.tick()
    execute(who, cv_run)
    chained = [j["args"][0] for j in who.queue.jobs if j["function"] == "run_agent"]
    if chained:
        execute(who, uuid.UUID(chained[0]))
    else:  # the person's runs were busy: the tick writes the letter
        (letter_run,) = world.tick()
        execute(who, letter_run)
    assert world.tick() == []
    with world.factory() as db:
        kinds = db.execute(
            sa.select(Document.kind, sa.func.count(DocumentVersion.id))
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.posting_id == posting, Document.scope == Scope.posting)
            .group_by(Document.kind)
        ).all()
    assert dict(kinds) == {Kind.cv: 1, Kind.letter: 1}
    assert find([planted], logs=caplog.text) == []


# --- the cron -----------------------------------------------------------------------------------


def test_the_worker_runs_the_tick_every_minute_on_the_documents_queue() -> None:
    from app import worker

    spec = worker.documents_queue()
    assert "documents_tick" in [found.name for found in spec.functions]
    crons = {found.name: found for found in spec.cron_jobs}
    assert crons["cron:documents_tick"].second == worker.DOCUMENTS_TICK_SECOND


def test_the_cron_job_takes_ids_only_and_queues_the_runs(world: World) -> None:
    import asyncio

    world.posting(80)
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
