"""D10: the tick on a real Postgres at head, two e2e people, the fake backend for the one test
that runs the writers end to end. No test sleeps or reads the clock: every pass gets its `at`.

The first base CV built from the profile (and nothing from an empty profile); postings inserted
by a sweep and by hand both picked up; the threshold at, under, without a fit, lowered (the last
30 days now written, highest fit first) and raised (nothing removed); 31 days old; a never apply
rule; `auto` off; the consent and the credential (waiting, nothing counted, then started); the
safety ceiling in America/New_York while the server is in UTC; one automatic run at a time; a
posting skipped or fallen under the threshold while waiting (cancelled); the retry once; the
default base CV changed; legacy files; the tick lock held by another pass; and the canary.

Never two starts: a click that read the open generation before the tick started it, and a click
committed between the tick's read and its start (one run either way, no exception, the next
person still handled). A run that ended without its final hook (reaped, killed, cancelled while
queued): untouched within the grace, then settled, the next posting started and the posting
retried once. One person's failure never ends the pass; a start that raised is never counted; a
waiting automatic base CV with `auto` off; no base CV and no employer still cancels.

The letter of a written CV is work for the tick after the CV moved its posting to `cv_ready`: the
one the chain left `queued` (the person's runs all busy) and the one it never created (the hook
cut short), never one of a posting applied meanwhile or of a `cv_ready` posting without a written
CV; and it gets what the pair's ceiling has left after the CV's run.

The posting's lock before the document's: a move to applied beside the tick's letter start and
beside a recovered CV retried at once (two sessions, both commit), and a move to applied
committed after the pass read the status (the CV or the letter cancelled, never started)."""

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
from app.documents import (  # noqa: F401 (writers registers the writers, freeze its hook)
    auto_router,
    consent,
    estimate,
    freeze,
    generate,
    store,
    tick,
    writers,
)
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
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
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
    short_locks,
)
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.documents.writer_support import Person, execute

__all__ = ["engine", "factory", "migrated_url", "short_locks"]

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
    found = world.generations(posting)  # one frozen clock: tell them apart by status
    (first,) = [g for g in found if g["status"] is GenerationStatus.failed]
    (second,) = [g for g in found if g["status"] is GenerationStatus.running]
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


# --- never two starts: a click and the tick -----------------------------------------------------


def click(who: World, posting: uuid.UUID) -> None:
    """A "Write now" of the posting's CV, as `auto_router.write_now` does it, in its own session."""
    with who.factory() as db:
        store.lock_posting(db, who.user, posting)
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
        )
        generate.start(db, made, at=AT, settings=who.settings)
        store.commit(db)


def runs_of(who: World, posting: uuid.UUID) -> int:
    with who.factory() as db:
        return int(
            db.execute(
                sa.text("SELECT count(*) FROM agent_runs WHERE user_id = :u AND job_id = :j"),
                {"u": who.user, "j": posting},
            ).scalar_one()
        )


def triggers(
    who: World, posting: uuid.UUID
) -> set[tuple[GenerationTrigger, GenerationStatus, bool]]:
    # one frozen clock: the rows share created_at, so compare them as a set
    return {
        (g["trigger"], g["status"], g["run_id"] is not None)
        for g in posting_cv(who.generations(posting))
    }


def test_a_tick_during_a_clicks_read_is_held_off_and_one_run_starts(
    factory: sessionmaker[Session],
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    short_locks: None,
) -> None:
    who = World(factory, migrated_url, USER_A, credential=False)
    posting = who.posting(80)
    who.tick()  # waiting with `credential`
    who.credential()
    with factory() as db:
        cv_id = store.get_or_create(db, who.user, posting, Kind.cv, at=AT).id
        db.commit()
    real = store.open_generation
    meanwhile: list[list[uuid.UUID]] = []

    def read_then_tick(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Any:
        found = real(db, user_id, document_id)
        if document_id == cv_id and not meanwhile:
            meanwhile.append(who.tick())  # the cron, in its own transaction, between read and write
        return found

    monkeypatch.setattr(store, "open_generation", read_then_tick)
    click(who, posting)
    assert meanwhile == [[]]  # the click holds the document: the tick leaves it to the next pass
    assert runs_of(who, posting) == 1
    assert triggers(who, posting) == {
        (GenerationTrigger.auto, GenerationStatus.cancelled, False),
        (GenerationTrigger.click, GenerationStatus.running, True),
    }
    assert who.tick() == []


def test_a_tick_that_started_first_makes_the_click_a_409(world: World) -> None:
    posting = world.posting(80)
    assert len(world.tick()) == 1
    with pytest.raises(auto_router.HTTPException) as refused:
        click(world, posting)
    assert refused.value.status_code == 409
    assert runs_of(world, posting) == 1


@pytest.mark.parametrize("state", ["waiting", "queued"])
def test_a_click_committed_between_the_ticks_read_and_its_start_wins_and_the_pass_goes_on(
    factory: sessionmaker[Session],
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    state: str,
) -> None:
    alex = World(factory, migrated_url, USER_A, credential=state == "queued")
    blake = World(factory, migrated_url, USER_B)
    settings = alex.settings.model_copy(update={"documents_auto_at_once": 1})
    if state == "waiting":  # waiting with `credential`; the credential arrives before the pass
        posting = alex.posting(80)
        alex.tick()
        alex.credential()
    else:  # queued behind one running; that one fails, so the slot is free for this pass
        alex.posting(90)
        posting = alex.posting(80)
        tick.tick(factory, AT, settings, only=[alex.user])
        alex.finish("failed", "run_failed")
    assert {g["status"].value for g in posting_cv(alex.generations(posting))} == {state}
    blake_posting = blake.posting(85)
    real = tick.not_started
    clicked: list[uuid.UUID] = []

    def read_then_click(db: Session, user_id: uuid.UUID) -> list[tick.Waiting]:
        found = real(db, user_id)
        if user_id == alex.user and not clicked:
            click(alex, posting)  # committed after the tick's read, before its start
            clicked.append(posting)
        return found

    monkeypatch.setattr(tick, "not_started", read_then_click)
    with caplog.at_level(logging.INFO, logger="app.documents.tick"):
        runs = tick.tick(factory, AT, settings, only=[alex.user, blake.user])
    assert clicked == [posting]
    # alex's pass skipped the cancelled generation and committed: no error, nothing held
    held = ("documents.tick.person_failed", "documents.tick.busy")
    assert not [r for r in caplog.records if r.getMessage() in held]
    assert runs_of(alex, posting) == 1
    assert triggers(alex, posting) == {
        (GenerationTrigger.auto, GenerationStatus.cancelled, False),
        (GenerationTrigger.click, GenerationStatus.running, True),
    }
    blake_cv = posting_cv(blake.generations(blake_posting))
    assert [g["status"] for g in blake_cv] == [GenerationStatus.running]
    assert runs == [blake_cv[0]["run_id"]]


# --- a run that ended without its final hook ----------------------------------------------------


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


def test_the_grace_is_over_the_runners_final_hook_timeout() -> None:
    assert runner.FINAL_HOOK_TIMEOUT_S < tick.HOOK_GRACE_S


def test_a_reaped_run_is_settled_after_the_grace_the_next_posting_starts_and_it_is_retried_once(
    world: World,
) -> None:
    first = world.posting(90)
    world.tick()
    ended = AT + timedelta(seconds=10)
    end_runs(world, "failed", "The run stopped reporting.", ended)
    second = world.posting(85)
    grace = timedelta(seconds=tick.HOOK_GRACE_S)
    assert world.tick(ended + grace) == []  # a hook merely late still settles its own run
    assert [g["status"] for g in posting_cv(world.generations(first))] == [GenerationStatus.running]
    started = world.tick(ended + grace + timedelta(seconds=1))
    [failed] = posting_cv(world.generations(first))
    assert failed["status"] is GenerationStatus.failed
    with world.factory() as db:
        row = db.get(DocumentGeneration, failed["id"])
        assert row is not None
        assert (row.error, row.finished_at) == ("run_failed", ended)
    [running] = posting_cv(world.generations(second))
    assert running["status"] is GenerationStatus.running
    assert started == [running["run_id"]]
    world.finish("failed", "run_failed", at=ended + grace)  # the second fails too
    assert world.tick(ended + timedelta(seconds=599)) == []
    assert len(world.tick(ended + timedelta(seconds=600))) == 1  # 600 s after the run ended
    assert [g["status"] for g in posting_cv(world.generations(first))] == [
        GenerationStatus.failed,
        GenerationStatus.running,
    ]


def test_a_killed_run_without_a_finish_time_is_recovered_on_the_next_pass(world: World) -> None:
    first = world.posting(90)
    world.tick()
    end_runs(world, "failed", "stale", None)  # its start time stands in for the end
    second = world.posting(85)
    started = world.tick(AT + timedelta(seconds=tick.HOOK_GRACE_S + 1))
    assert [g["status"] for g in posting_cv(world.generations(first))] == [GenerationStatus.failed]
    assert [g["run_id"] for g in posting_cv(world.generations(second))] == started
    assert len(started) == 1


@pytest.mark.parametrize(
    ("run", "error", "status", "code"),
    [
        ("failed", failures.TOKEN_LIMIT, GenerationStatus.failed, "token_limit"),
        ("failed", "The worker stopped.", GenerationStatus.failed, "run_failed"),
        ("done", None, GenerationStatus.failed, "run_failed"),
        ("cancelled", None, GenerationStatus.cancelled, None),
        ("running", None, GenerationStatus.running, None),
        ("queued", None, GenerationStatus.running, None),
    ],
)
def test_a_run_settled_by_the_tick_takes_the_runs_outcome_and_a_live_run_is_never_touched(
    world: World, run: str, error: str | None, status: GenerationStatus, code: str | None
) -> None:
    posting = world.posting(90)
    world.tick()
    end_runs(world, run, error, AT if run in ("failed", "done", "cancelled") else None)
    world.tick(AT + timedelta(seconds=tick.HOOK_GRACE_S + 1))
    found = posting_cv(world.generations(posting))[0]  # a stop at the ceiling retries at once
    with world.factory() as db:
        row = db.get(DocumentGeneration, found["id"])
        assert row is not None
        assert (row.status, row.error) == (status, code)


def test_a_base_cv_build_that_ended_without_its_hook_is_settled_too(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False)
    who.tick()
    end_runs(who, "failed", "stale", AT)
    who.tick(AT + timedelta(days=1))
    base = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert [g["status"] for g in base] == [GenerationStatus.failed]


# --- one person's failure, a start that raised, auto off, nothing to write from -----------------


def test_an_unexpected_error_for_one_person_never_ends_the_pass_for_the_next(
    factory: sessionmaker[Session],
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alex = World(factory, migrated_url, USER_A)
    blake = World(factory, migrated_url, USER_B)
    alex.posting(90)
    blake_posting = blake.posting(80)
    planted = marker()
    real = tick.eligible

    def broken(db: Session, user_id: uuid.UUID, *args: Any) -> list[tick.Candidate]:
        if user_id == alex.user:
            raise RuntimeError(planted)
        return real(db, user_id, *args)

    monkeypatch.setattr(tick, "eligible", broken)
    with caplog.at_level(logging.INFO, logger="app.documents.tick"):
        runs = tick.tick(factory, AT, alex.settings, only=[alex.user, blake.user])
    assert alex.generations() == []  # rolled back
    assert [g["run_id"] for g in posting_cv(blake.generations(blake_posting))] == runs
    assert len(runs) == 1
    failed = [r for r in caplog.records if r.getMessage() == "documents.tick.person_failed"]
    assert [(r.user_id, r.error) for r in failed] == [(str(alex.user), "RuntimeError")]  # type: ignore[attr-defined]
    assert planted not in caplog.text


def test_a_start_that_raised_is_never_counted_against_the_ceiling(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    posting = world.posting(90)

    def refuse(*_: Any, **__: Any) -> Any:
        raise ValueError("refused")

    monkeypatch.setattr(tick.generate, "start", refuse)
    assert world.tick() == []
    [found] = posting_cv(world.generations(posting))
    assert (found["status"], found["ceiling_day"]) == (GenerationStatus.queued, None)
    with world.factory() as db:
        assert tick.started_today(db, world.user, TODAY) == 0


def test_with_auto_off_a_waiting_automatic_base_cv_build_does_not_start(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, base=False, credential=False)
    who.tick()
    [base] = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert (base["status"], base["wait_reason"]) == (
        GenerationStatus.waiting,
        GenerationWait.credential,
    )
    who.search(auto=False)
    who.credential()
    assert who.tick() == []
    [base] = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert base["status"] is GenerationStatus.waiting
    who.search(auto=True)
    started = who.tick()
    [base] = [g for g in who.generations() if g["mode"] is GenerationMode.base_profile]
    assert (base["status"], started) == (GenerationStatus.running, [base["run_id"]])


def test_without_a_base_cv_or_an_employer_what_waits_is_still_cancelled(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = World(factory, migrated_url, USER_A, profile=False, credential=False)
    posting = who.posting(80)
    who.tick()  # a base CV exists: the posting waits for the credential
    assert [g["status"] for g in posting_cv(who.generations(posting))] == [GenerationStatus.waiting]
    with factory() as db:  # every base CV deleted, and the profile has no employer
        store.delete_base(db, who.user, who.base, AT)
        db.commit()
    who.set_job(posting, status="skipped")
    assert who.tick() == []
    assert [g["status"] for g in posting_cv(who.generations(posting))] == [
        GenerationStatus.cancelled
    ]


# --- the letter of a written CV -----------------------------------------------------------------


def _letters(world: World, posting: uuid.UUID) -> list[dict[str, Any]]:
    return [g for g in world.generations(posting) if g["kind"] is Kind.letter]


def _busy(world: World) -> list[uuid.UUID]:
    """The person's runs all taken by runs of another kind (as ingestion's or a chat's)."""
    with world.factory() as db:
        made = [
            runs.create_run(db, world.user, "documents_cv", None, {}, "m", AT).id
            for _ in range(runs.ACTIVE_PER_USER)
        ]
        db.commit()
    return made


def _free(world: World, busy: list[uuid.UUID]) -> None:
    with world.factory() as db:
        db.execute(sa.text("UPDATE agent_runs SET status = 'done' WHERE id = ANY(:r)"), {"r": busy})
        db.commit()


def test_a_letter_the_busy_chain_left_queued_starts_after_the_cv_moved_the_posting(
    world: World,
) -> None:
    posting = world.posting(80)
    (cv_run,) = world.tick()
    world.finish()
    busy = _busy(world)
    assert generate.start_letter(world.factory, cv_run, AT, world.settings) is None
    assert [(g["status"], g["trigger"]) for g in _letters(world, posting)] == [
        (GenerationStatus.queued, GenerationTrigger.auto)
    ]
    world.set_job(posting, status=JobStatus.cv_ready)  # the CV's one page render
    assert world.tick(AT + timedelta(minutes=1)) == []  # still busy: kept, not cancelled
    assert [g["status"] for g in _letters(world, posting)] == [GenerationStatus.queued]
    _free(world, busy)
    started = world.tick(AT + timedelta(minutes=2))
    (letter,) = _letters(world, posting)
    assert letter["status"] is GenerationStatus.running
    assert started == [letter["run_id"]]


def test_a_cv_done_without_its_letter_gets_it_from_the_tick_once_the_posting_is_cv_ready(
    world: World,
) -> None:
    posting = world.posting(80)
    world.tick()
    world.finish()  # the hook was cut short: no letter, nothing waits
    world.set_job(posting, status=JobStatus.cv_ready)
    started = world.tick(AT + timedelta(minutes=1))
    (letter,) = _letters(world, posting)
    assert (letter["status"], letter["trigger"]) == (
        GenerationStatus.running,
        GenerationTrigger.auto,
    )
    assert started == [letter["run_id"]]


@pytest.mark.parametrize("status", [JobStatus.staged, JobStatus.applied, JobStatus.skipped])
def test_a_queued_letter_of_a_posting_moved_past_cv_ready_is_cancelled_never_started(
    world: World, status: JobStatus
) -> None:
    posting = world.posting(80)
    (cv_run,) = world.tick()
    world.finish()
    busy = _busy(world)
    generate.start_letter(world.factory, cv_run, AT, world.settings)
    _free(world, busy)
    world.set_job(posting, status=status)
    assert world.tick(AT + timedelta(minutes=1)) == []
    assert [g["status"] for g in _letters(world, posting)] == [GenerationStatus.cancelled]


def _apply(world: World, posting: uuid.UUID) -> None:
    """The person marks the posting applied, as the jobs service does: the posting locked for
    update, then the freeze of D14 locks its CV and its letter."""
    with world.factory() as db:
        job = jobs_service.lock_job(db, world.user, posting)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.applied, AT)
        store.commit(db)


def _queued_letter(world: World) -> tuple[uuid.UUID, uuid.UUID]:
    """A `cv_ready` posting whose letter the busy chain left `queued`, the runs free again:
    (posting, letter generation)."""
    posting = world.posting(80)
    (cv_run,) = world.tick()
    world.finish()
    busy = _busy(world)
    generate.start_letter(world.factory, cv_run, AT, world.settings)
    _free(world, busy)
    world.set_job(posting, status=JobStatus.cv_ready)
    (letter,) = _letters(world, posting)
    return posting, letter["id"]


def test_a_move_to_applied_waits_behind_the_ticks_letter_start_and_both_commit(
    world: World, engine: sa.Engine
) -> None:
    """The tick claims the letter, then its run's row needs the posting; a move to applied locks
    the posting, then the freeze locks the letter. With the letter locked first, a circle
    Postgres broke (the person's move answered 409). The claim now takes the posting first."""
    posting, letter = _queued_letter(world)
    outcome: dict[str, Any] = {}
    with world.factory() as db:
        generation = db.get(DocumentGeneration, letter)
        assert generation is not None
        assert tick._claim(db, generation, at=AT)
        mover = in_thread(lambda: _apply(world, posting), outcome)
        wait_until_blocked(engine, [mover], 1)
        run = generate.start(db, generation, at=AT, settings=world.settings)
        store.commit(db)
    mover.join()
    assert "error" not in outcome, outcome.get("error")
    (row,) = _letters(world, posting)
    assert (row["status"], row["run_id"]) == (GenerationStatus.running, run.id)
    with world.factory() as db:
        assert db.get(Job, posting).status is JobStatus.applied  # type: ignore[union-attr]


def test_a_move_to_applied_waits_behind_a_recovered_cv_retried_at_once_and_both_commit(
    world: World, engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pass keeps the CV it recovered locked, and a CV that stopped at its ceiling is retried
    at once: its run's row needs the posting. A move to applied in between locked the posting,
    then waited for the CV: a circle. The recovery now takes the posting first."""
    posting = world.posting(80)
    world.tick()
    ended = AT + timedelta(seconds=10)
    end_runs(world, "failed", failures.TOKEN_LIMIT, ended)
    real = tick.recover
    outcome: dict[str, Any] = {}
    movers: list[Any] = []

    def recover_then_apply(db: Session, user_id: uuid.UUID, at: datetime) -> int:
        settled = real(db, user_id, at)
        movers.append(in_thread(lambda: _apply(world, posting), outcome))
        wait_until_blocked(engine, movers, 1)
        return settled

    monkeypatch.setattr(tick, "recover", recover_then_apply)
    started = world.tick(ended + timedelta(seconds=tick.HOOK_GRACE_S + 1))
    movers[0].join()
    assert "error" not in outcome, outcome.get("error")
    cv = posting_cv(world.generations(posting))
    assert [g["status"] for g in cv] == [GenerationStatus.failed, GenerationStatus.running]
    assert started == [cv[-1]["run_id"]]
    with world.factory() as db:
        assert db.get(Job, posting).status is JobStatus.applied  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", [Kind.cv, Kind.letter])
def test_a_move_to_applied_after_the_ticks_read_cancels_what_it_would_start(
    world: World, monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    if kind is Kind.letter:
        posting, _ = _queued_letter(world)
    else:
        posting = world.posting(80)
    real = tick.eligible
    moved: list[uuid.UUID] = []

    def read_then_apply(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        if not moved:
            _apply(world, posting)  # committed after the pass read the status, before its start
            moved.append(posting)
        return found

    monkeypatch.setattr(tick, "eligible", read_then_apply)
    assert world.tick(AT + timedelta(minutes=1)) == []
    assert moved == [posting]
    owned = [g for g in world.generations(posting) if g["kind"] is kind]
    assert [(g["status"], g["run_id"]) for g in owned][-1:] == [(GenerationStatus.cancelled, None)]
    assert runs_of(world, posting) == (1 if kind is Kind.letter else 0)  # the CV's run only


def test_a_cv_ready_posting_without_a_written_cv_gets_nothing(world: World) -> None:
    posting = world.posting(80, status="cv_ready")
    assert world.tick() == []
    assert world.generations(posting) == []


def test_the_ticks_letter_gets_what_the_pairs_ceiling_has_left_after_the_cvs_run(
    world: World,
) -> None:
    posting = world.posting(80)
    (cv_run,) = world.tick()
    with world.factory() as db:
        db.execute(
            sa.text(
                "UPDATE agent_runs SET input_tokens = 30000, output_tokens = 4000 WHERE id = :r"
            ),
            {"r": cv_run},
        )
        db.commit()
    world.finish()
    world.tick(AT + timedelta(minutes=1))
    (cv,) = posting_cv(world.generations(posting))
    (letter,) = _letters(world, posting)
    assert letter["token_ceiling"] == cv["token_ceiling"] - 34_000
