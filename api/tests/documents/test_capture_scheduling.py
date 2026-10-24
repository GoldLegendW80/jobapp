"""606 A1: atomic capture scheduling, separate state and finite recovery."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any, NoReturn

import pytest
import sqlalchemy as sa

from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.definitions import FinalRun
from app.agents.models import AgentRun, RunStatus
from app.documents import (
    design_capture,
    generate,
    store,
    tick,
    writer_base,
    writer_design,
)
from app.documents.models import (
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    RenderState,
)
from app.ingestion.models import IngestionDocument
from tests.documents.capture_support import captures, conversion, world
from tests.documents.test_base_api import alex, queue
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["alex", "api", "app", "clock", "engine", "factory", "migrated_url", "queue", "world"]
BASES = "/api/documents/base-cvs"


def test_legacy_and_capture_state_are_separate(world: Any) -> None:
    before = world.api.get(BASES, token=world.token).json()["base_cvs"]
    legacy = next(row for row in before if row["id"] == str(world.base))
    assert legacy["design_generation"] is None
    with world.factory() as db:
        made = store.create_generation(
            db,
            world.user,
            world.base,
            GenerationMode.base_upload,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
            status=GenerationStatus.done,
        )
        made.finished_at = world.clock()
        capture = store.create_generation(
            db,
            world.user,
            world.base,
            GenerationMode.template_capture,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
            status=GenerationStatus.failed,
        )
        capture.error = "invalid_output"
        db.commit()
    row = next(
        row
        for row in world.api.get(BASES, token=world.token).json()["base_cvs"]
        if row["id"] == str(world.base)
    )
    assert row["generation"]["id"] == str(made.id)
    assert row["generation"]["status"] == "done"
    assert row["design_generation"]["id"] == str(capture.id)
    assert row["design_generation"]["error"] == "invalid_output"


def test_capture_intent_exists_in_exact_render_handoff_window(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = conversion(world, monkeypatch)

    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(*_: Any) -> None:
            entered.set()
            await release.wait()

        monkeypatch.setattr(writer_base, "_queue_render", paused)
        task = asyncio.create_task(writer_base._on_final(final))
        await entered.wait()
        try:
            with world.factory() as db:
                db.execute(
                    sa.update(Document)
                    .where(Document.id == world.base)
                    .values(render_state=RenderState.idle)
                )
                db.commit()
            row = next(
                row
                for row in world.api.get(BASES, token=world.token).json()["base_cvs"]
                if row["id"] == str(world.base)
            )
            assert row["generation"]["status"] == "done"
            assert row["render_state"] == "idle"
            assert row["design_generation"]["status"] in ("queued", "waiting")
            assert len(captures(world)) == 1
            assert captures(world)[0].run_id is None
        finally:
            release.set()
            await task

    asyncio.run(exercise())


def test_replaying_content_final_does_not_start_a_new_capture(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = conversion(world, monkeypatch)
    with world.factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET bytes=NULL WHERE id=:i"), {"i": world.upload}
        )
        db.commit()
    asyncio.run(writer_base._on_final(final))
    first = captures(world)
    assert len(first) == 1
    assert first[0].status is GenerationStatus.failed
    asyncio.run(writer_base._on_final(final))
    assert [row.id for row in captures(world)] == [row.id for row in first]


@pytest.mark.parametrize("mode", [GenerationMode.template_capture, GenerationMode.base_upload])
@pytest.mark.parametrize("status", [RunStatus.failed, RunStatus.cancelled, RunStatus.done])
def test_lost_final_hook_settles_only_after_existing_grace(
    world: Any, mode: GenerationMode, status: RunStatus
) -> None:
    with world.factory() as db:
        generation = store.create_generation(
            db,
            world.user,
            world.base,
            mode,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
        )
        from app.documents import writer_design

        start = (
            writer_design.start if mode is GenerationMode.template_capture else writer_base.start
        )
        run = start(db, generation, at=world.clock(), settings=world.app.state.settings)
        run.status, run.finished_at, run.error = status, world.clock(), "lost"
        db.commit()
    with world.factory() as db:
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S)) == 0
        )
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S + 1))
            == 1
        )
        db.commit()
        generation = db.get(DocumentGeneration, generation.id)
        assert generation is not None
        assert generation.status is (
            GenerationStatus.cancelled if status is RunStatus.cancelled else GenerationStatus.failed
        )
        assert generation.error == (None if status is RunStatus.cancelled else "run_failed")
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=2 * tick.HOOK_GRACE_S))
            == 0
        )
    assert (GenerationMode.posting, GenerationMode.base_profile) == tick.COUNTED_MODES


def prepared(world: Any) -> uuid.UUID:
    with world.factory() as db:
        generation = store.create_generation(
            db,
            world.user,
            world.base,
            GenerationMode.template_capture,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={"low": 1, "high": 2, "ceiling": 10},
            token_ceiling=10,
        )
        db.commit()
        return generation.id


@pytest.mark.parametrize("gone", ["credential", "consent", "bytes", "source"])
def test_prepared_capture_rechecks_its_binding_and_permission(
    world: Any, gone: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=design_capture.logger.name)
    generation_id = prepared(world)
    with world.factory() as db:
        if gone == "credential":
            from app.agents import credentials

            credentials.delete(db, world.user)
        elif gone == "consent":
            db.execute(sa.text("DELETE FROM document_consents WHERE user_id=:u"), {"u": world.user})
        elif gone == "source":
            db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == world.upload))
        else:
            db.execute(
                sa.update(IngestionDocument)
                .where(IngestionDocument.id == world.upload)
                .values(raw=None)
            )
        db.commit()
    with world.factory() as db:
        started = design_capture.start_ready(
            db, world.user, at=world.clock(), settings=world.app.state.settings
        )
        db.commit()
        assert started == []
        generation = db.get(DocumentGeneration, generation_id)
        assert generation is not None
        assert generation.run_id is None
        assert generation.token_ceiling == 10
        logged = [
            record.generation_id
            for record in caplog.records
            if record.getMessage() == "documents.design.no_bytes"
        ]
        if gone in ("source", "bytes"):
            assert generation.status is GenerationStatus.failed
            assert generation.error == "binding_gone"
            assert logged == [str(generation_id)]
        else:
            assert generation.status is GenerationStatus.waiting
            assert generation.wait_reason is GenerationWait(gone)
            assert logged == []


@pytest.mark.parametrize("reason", [GenerationWait.consent, GenerationWait.credential])
def test_capture_whose_wait_ended_at_the_run_limit_is_queued_without_its_old_reason(
    world: Any, reason: GenerationWait
) -> None:
    generation_id = prepared(world)
    with world.factory() as db:
        db.execute(
            sa.update(DocumentGeneration)
            .where(DocumentGeneration.id == generation_id)
            .values(status=GenerationStatus.waiting, wait_reason=reason)
        )
        for _ in range(agent_runs.ACTIVE_PER_USER):
            agent_runs.create_run(db, world.user, "documents_cv", None, {}, "m", world.clock())
        db.commit()
    with world.factory() as db:
        started = design_capture.start_ready(
            db, world.user, at=world.clock(), settings=world.app.state.settings
        )
        db.commit()
    assert started == []
    shown = next(
        row["design_generation"]
        for row in world.api.get(BASES, token=world.token).json()["base_cvs"]
        if row["id"] == str(world.base)
    )
    assert (shown["id"], shown["status"], shown["wait_reason"]) == (
        str(generation_id),
        "queued",
        None,
    )


@pytest.mark.parametrize("second", ["click", "tick", "hook"])
def test_two_starts_of_one_capture_at_once_make_one_run(
    world: Any, engine: sa.Engine, second: str
) -> None:
    generation_id = prepared(world)
    settings, jobs = world.app.state.settings, len(world.app.state.queue.jobs)

    def other() -> Any:
        if second == "hook":
            final = FinalRun(
                uuid.uuid4(),
                world.user,
                writer_base.NAME,
                RunStatus.done,
                world.factory,
                world.clock,
                settings,
                {generate.QUEUE_SERVICE: world.app.state.queue},
            )
            return asyncio.run(design_capture.enqueue(final, generation_id))
        with world.factory() as db:
            if second == "click":
                found: Any = design_capture.start_prepared(
                    db, world.user, generation_id, at=world.clock(), settings=settings
                )
            else:
                found = writer_base.start_ready(db, world.user, at=world.clock(), settings=settings)
            db.commit()
            return found

    outcome: dict[str, Any] = {}
    with world.factory() as db:
        first = design_capture.start_prepared(
            db, world.user, generation_id, at=world.clock(), settings=settings
        )
        threads = [in_thread(other, outcome)]
        wait_until_blocked(engine, threads, 1)
        db.commit()
    threads[0].join()
    assert outcome.get("error") is None
    assert outcome["result"] in (None, [])
    with world.factory() as db:
        made = db.scalars(
            sa.select(AgentRun.id).where(
                AgentRun.user_id == world.user, AgentRun.agent == writer_design.NAME
            )
        ).all()
    assert first is not None
    assert made == [first]
    assert len(world.app.state.queue.jobs) == jobs


def test_content_and_capture_intent_commit_together(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = conversion(world, monkeypatch)
    commit = store.commit
    observed = []

    def inspect(db: Any) -> None:
        content = db.scalar(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == final.run_id)
        )
        if not observed and content is not None and content.status is GenerationStatus.done:
            own_capture = db.scalar(
                sa.select(DocumentGeneration).where(
                    DocumentGeneration.document_id == world.base,
                    DocumentGeneration.mode == GenerationMode.template_capture,
                )
            )
            assert own_capture is not None
            assert own_capture.run_id is None
            with world.factory() as other:
                old_content = other.get(DocumentGeneration, content.id)
                assert old_content.status is GenerationStatus.running
                assert other.get(DocumentGeneration, own_capture.id) is None
            observed.append(own_capture.id)
        commit(db)

    monkeypatch.setattr(store, "commit", inspect)
    asyncio.run(writer_base._on_final(final))
    assert len(observed) == 1
    assert captures(world)[0].id == observed[0]


def test_interruption_before_capture_dispatch_is_started_by_tick(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = conversion(world, monkeypatch)

    async def interrupted() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def stop_here(*_: Any) -> None:
            entered.set()
            await release.wait()

        monkeypatch.setattr(writer_base, "_queue_render", stop_here)
        task = asyncio.create_task(writer_base._on_final(final))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(interrupted())
    intent = captures(world)
    assert len(intent) == 1
    assert intent[0].status is GenerationStatus.queued
    with world.factory() as db:
        started = writer_base.start_ready(
            db, world.user, at=world.clock(), settings=world.app.state.settings
        )
        db.commit()
    assert len(started) == 1
    assert [row.id for row in captures(world)] == [intent[0].id]
    assert captures(world)[0].status is GenerationStatus.running


@pytest.mark.parametrize("failure", ["after_insert", "database"])
def test_failed_capture_preparation_keeps_the_converted_content(
    world: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    final = conversion(world, monkeypatch)
    prepare = design_capture._prepare

    def failing(db: Any, document: Document, **kwargs: Any) -> NoReturn:
        if failure == "database":
            db.execute(sa.text("SELECT 1 / 0"))
        prepare(db, document, **kwargs)
        raise RuntimeError("invented preparation failure")

    monkeypatch.setattr(design_capture, "_prepare", failing)
    caplog.set_level(logging.INFO, logger=design_capture.logger.name)
    jobs = len(world.app.state.queue.jobs)
    asyncio.run(writer_base._on_final(final))
    with world.factory() as db:
        content = db.scalar(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == final.run_id)
        )
        assert content.status is GenerationStatus.done
        version = db.scalar(
            sa.select(DocumentVersion.number).where(DocumentVersion.generation_id == content.id)
        )
        assert version is not None
        assert version == content.version_number
    assert captures(world) == []
    assert len(world.app.state.queue.jobs) == jobs + 1  # the converted version's render only
    assert [
        (record.generation_id, record.error)
        for record in caplog.records
        if record.getMessage() == "documents.design.not_prepared"
    ] == [(str(content.id), "DataError" if failure == "database" else "RuntimeError")]


def test_source_deleted_after_content_was_read_does_not_undo_conversion(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = conversion(world, monkeypatch)
    with world.factory() as db:
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == world.upload))
        db.commit()
    asyncio.run(writer_base._on_final(final))
    with world.factory() as db:
        content = db.scalar(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == final.run_id)
        )
        assert content.status is GenerationStatus.done
    capture = captures(world)
    assert len(capture) == 1
    assert capture[0].status is GenerationStatus.failed
    assert capture[0].error == "binding_gone"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (failures.TOKEN_LIMIT, "token_limit"),
        (failures.BINDING_GONE, "binding_gone"),
        ("An invented provider failure.", "run_failed"),
    ],
)
def test_recovered_capture_keeps_the_code_of_its_run(world: Any, error: str, code: str) -> None:
    with world.factory() as db:
        generation, run_id = design_capture.request(
            db, world.user, world.base, at=world.clock(), settings=world.app.state.settings
        )
        run = db.get(AgentRun, run_id)
        run.status, run.error, run.finished_at = RunStatus.failed, error, world.clock()
        db.commit()
    with world.factory() as db:
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S + 1))
            == 1
        )
        db.commit()
    assert captures(world)[0].id == generation.id
    assert captures(world)[0].error == code


@pytest.mark.parametrize(
    ("error", "code"), [(failures.BINDING_GONE, "binding_gone"), ("lost", "run_failed")]
)
def test_recovered_conversion_keeps_the_code_of_its_run(world: Any, error: str, code: str) -> None:
    with world.factory() as db:
        generation = store.create_generation(
            db,
            world.user,
            world.base,
            GenerationMode.base_upload,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
        )
        run = writer_base.start(db, generation, at=world.clock(), settings=world.app.state.settings)
        run.status, run.error, run.finished_at = RunStatus.failed, error, world.clock()
        db.commit()
    with world.factory() as db:
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S + 1))
            == 1
        )
        db.commit()
        recovered = db.get(DocumentGeneration, generation.id)
        assert recovered is not None
        assert (recovered.status, recovered.error) == (GenerationStatus.failed, code)
