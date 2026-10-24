"""606 A1: atomic capture scheduling, separate state and finite recovery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.agents import failures
from app.agents.models import AgentRun, RunStatus
from app.documents import (
    design_capture,
    store,
    tick,
    writer_base,
)
from app.documents.models import (
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    RenderState,
)
from app.ingestion.models import IngestionDocument
from tests.documents.capture_support import captures, conversion, world
from tests.documents.test_base_api import alex, queue
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
def test_prepared_capture_rechecks_its_binding_and_permission(world: Any, gone: str) -> None:
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
        if gone in ("source", "bytes"):
            assert generation.status is GenerationStatus.failed
            assert generation.error == "binding_gone"
        else:
            assert generation.status is GenerationStatus.waiting
            assert generation.wait_reason is GenerationWait(gone)


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


def test_recovered_capture_preserves_token_limit_code(world: Any) -> None:
    with world.factory() as db:
        generation, run_id = design_capture.request(
            db, world.user, world.base, at=world.clock(), settings=world.app.state.settings
        )
        run = db.get(AgentRun, run_id)
        run.status, run.error, run.finished_at = (
            RunStatus.failed,
            failures.TOKEN_LIMIT,
            world.clock(),
        )
        db.commit()
    with world.factory() as db:
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S + 1))
            == 1
        )
        db.commit()
    assert captures(world)[0].id == generation.id
    assert captures(world)[0].error == "token_limit"
