"""606 A2: retry HTTP, early private cache boundary, concurrency and retry sinks."""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import queue as queues
from app.agents import runs
from app.agents.models import AgentRun, RunStatus
from app.documents import (
    store,
    tick,
)
from app.documents.models import (
    BaseSource,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
)
from app.ingestion.models import IngestionDocument
from tests.documents.canary import marker
from tests.documents.capture_support import captures, world
from tests.documents.test_base_api import alex, queue
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["alex", "api", "app", "clock", "engine", "factory", "migrated_url", "queue", "world"]
BASES = "/api/documents/base-cvs"


def response_generation(world: Any, response: Any) -> DocumentGeneration:
    assert response.status_code == 202, response.text
    assert response.headers.get("cache-control") == "private, no-store"
    value = response.json()
    assert set(value) == {"id", "status", "wait_reason", "error"}
    with world.factory() as db:
        generation = db.get(DocumentGeneration, uuid.UUID(value["id"]))
        assert generation is not None
        assert generation.user_id == world.user
        assert value == dict(
            id=str(generation.id),
            status=generation.status.value,
            wait_reason=generation.wait_reason.value if generation.wait_reason else None,
            error=generation.error,
        )
        return generation


def test_retry_commits_before_queue_and_only_sends_run_id(world: Any, queue: Any) -> None:
    original = queue.enqueue

    async def observed(name: str, *args: Any, **kwargs: Any) -> Any:
        with world.factory() as db:
            run = db.get(AgentRun, uuid.UUID(args[0]))
            assert run is not None
            assert run.user_id == world.user
            assert db.scalar(
                sa.select(DocumentGeneration.id).where(DocumentGeneration.run_id == run.id)
            )
        return await original(name, *args, **kwargs)

    queue.enqueue = observed
    generation = response_generation(world, world.api.post(world.url, token=world.token))
    assert generation.status is GenerationStatus.running
    assert generation.run_id is not None
    assert queue.jobs == [
        dict(
            function="run_agent",
            args=(str(generation.run_id),),
            queue=queues.AGENTS,
            job_id=queues.agent_run_job_id(generation.run_id),
        )
    ]


@pytest.mark.parametrize("reason", ["consent", "credential", "no_bytes"])
def test_retry_waits_or_fails_without_starting_a_model(world: Any, queue: Any, reason: str) -> None:
    with world.factory() as db:
        if reason == "consent":
            db.execute(sa.text("DELETE FROM document_consents WHERE user_id=:u"), {"u": world.user})
        elif reason == "credential":
            from app.agents import credentials

            credentials.delete(db, world.user)
        else:
            db.execute(
                sa.text("UPDATE ingestion_documents SET bytes=NULL WHERE id=:i"),
                {"i": world.upload},
            )
        db.commit()
    generation = response_generation(world, world.api.post(world.url, token=world.token))
    assert generation.run_id is None
    assert queue.jobs == []
    assert generation.status.value == ("failed" if reason == "no_bytes" else "waiting")
    assert generation.error == ("binding_gone" if reason == "no_bytes" else None)
    assert (generation.wait_reason.value if generation.wait_reason else None) == (
        None if reason == "no_bytes" else reason
    )


@pytest.mark.parametrize(
    "mode", [GenerationMode.base_profile, GenerationMode.chat, GenerationMode.template_capture]
)
def test_any_open_generation_refuses_retry(world: Any, mode: GenerationMode) -> None:
    with world.factory() as db:
        store.create_generation(
            db,
            world.user,
            world.base,
            mode,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
        )
        db.commit()
    answer = world.api.post(world.url, token=world.token)
    assert answer.status_code == 409, answer.text
    assert answer.headers.get("cache-control") == "private, no-store"


def test_missing_and_other_person_have_the_same_private_404(world: Any) -> None:
    other = world.api.login_token("e2e-docs-base-alex@example.org")
    # A distinct user's document, not a guessed source identifier.
    from tests.identity.api_helpers import make_user

    stranger = make_user(world.factory, email="e2e-capture-stranger@example.org", name="Invented")
    with world.factory() as db:
        base = store.create_base(db, stranger.id, "Other", BaseSource.built, at=world.clock())
        db.commit()
    missing = world.api.post(f"/api/documents/{uuid.uuid4()}/design/capture", token=other)
    foreign = world.api.post(f"/api/documents/{base.id}/design/capture", token=other)
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert (
        missing.headers.get("cache-control")
        == foreign.headers.get("cache-control")
        == "private, no-store"
    )


def test_owned_non_upload_is_422(world: Any) -> None:
    with world.factory() as db:
        built = store.create_base(db, world.user, "Built", BaseSource.built, at=world.clock())
        db.commit()
    answer = world.api.post(f"/api/documents/{built.id}/design/capture", token=world.token)
    assert answer.status_code == 422, answer.text
    assert answer.headers.get("cache-control") == "private, no-store"


@pytest.mark.parametrize(
    ("kind", "expected"), [("anonymous", 401), ("csrf", 403), ("invalid", 422), ("method", 405)]
)
def test_early_errors_are_private_and_create_nothing(world: Any, kind: str, expected: int) -> None:
    answer = world.api.request(
        "GET" if kind == "method" else "POST",
        world.url if kind != "invalid" else "/api/documents/not-a-uuid/design/capture",
        token=None if kind == "anonymous" else world.token,
        csrf=kind != "csrf",
    )
    assert answer.status_code == expected, answer.text
    assert answer.headers.get("cache-control") == "private, no-store"
    assert captures(world) == []


def test_unhandled_response_is_private_and_fixed(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("synthetic boundary failure")

    monkeypatch.setattr(store, "lock_document", broken)
    with TestClient(world.app, raise_server_exceptions=False) as client:
        answer = Api(client).post(world.url, token=world.token)
    assert answer.status_code == 500, answer.text
    assert answer.json() == {"detail": "internal server error"}
    assert answer.headers.get("cache-control") == "private, no-store"


def test_two_concurrent_clicks_create_one_capture(world: Any) -> None:
    ready = Barrier(2)

    def click() -> int:
        client = TestClient(world.app)
        try:
            ready.wait()
            return Api(client).post(world.url, token=world.token).status_code
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert sorted(results) == [202, 409]
    assert len(captures(world)) == 1


def test_queue_loss_is_terminal_then_requires_explicit_retry(world: Any, queue: Any) -> None:
    queue.down = True
    generation = response_generation(world, world.api.post(world.url, token=world.token))
    with world.factory() as db:
        run = db.get(AgentRun, generation.run_id)
        assert run.status is RunStatus.queued
        assert (
            runs.reap_stale(
                db, world.clock() + timedelta(seconds=2), timedelta(minutes=5), timedelta(seconds=1)
            )
            == 1
        )
        db.commit()
        assert (
            tick.recover(db, world.user, world.clock() + timedelta(seconds=tick.HOOK_GRACE_S + 3))
            == 1
        )
        db.commit()
    assert captures(world)[0].status is GenerationStatus.failed
    assert captures(world)[0].error == "run_failed"
    assert queue.jobs == []
    queue.down = False
    retried = response_generation(world, world.api.post(world.url, token=world.token))
    assert retried.id != generation.id
    assert len(queue.jobs) == 1


def test_original_canary_is_absent_from_retry_sinks(
    world: Any, queue: Any, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    with world.factory() as db:
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == world.upload)
            .values(raw=b"%PDF-1.7 " + planted.encode(), name=planted + ".pdf")
        )
        db.commit()
    caplog.set_level(logging.INFO, logger="app.documents")
    generation = response_generation(world, world.api.post(world.url, token=world.token))
    with world.factory() as db:
        run = db.get(AgentRun, generation.run_id)
        assert planted not in json.dumps(run.input)
    assert planted not in json.dumps(queue.jobs)
    assert planted not in json.dumps([vars(record) for record in caplog.records], default=str)


def test_unhandled_document_exception_keeps_private_context_out_of_full_logs(
    world: Any, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    planted = marker()
    path = "/api/documents/test-capture/error"

    def broken() -> None:
        raise RuntimeError(planted)

    world.app.add_api_route(path, broken, methods=["GET"])
    caplog.set_level(logging.INFO)
    with TestClient(world.app, raise_server_exceptions=False) as client:
        answer = Api(client).get(path, token=world.token)
    assert answer.status_code == 500
    assert answer.json() == {"detail": "internal server error"}
    assert any(
        record.name == "app" and record.levelno >= logging.ERROR for record in caplog.records
    )
    recorded = json.dumps([vars(record) for record in caplog.records], default=str)
    output = capfd.readouterr()
    assert {
        "cache": answer.headers.get("cache-control"),
        "private_context_in_records": planted in recorded,
        "private_context_in_output": planted in output.out + output.err,
        "private_context_in_response": planted in answer.text,
    } == {
        "cache": "private, no-store",
        "private_context_in_records": False,
        "private_context_in_output": False,
        "private_context_in_response": False,
    }
    assert captures(world) == []


@pytest.mark.parametrize(
    ("path", "private"),
    [
        ("/api/documents", True),
        ("/api/documents/", True),
        ("/api/documents/anything/deeper", True),
        ("/api/documentsx", False),
        ("/api/document-templates", False),
        ("/api/document-templates/classic/thumbnail", False),
        ("/api/jobs", False),
        ("/", False),
    ],
)
def test_private_cache_rule_covers_only_the_documents_prefix(path: str, private: bool) -> None:
    import asyncio

    from starlette.requests import Request
    from starlette.responses import Response

    from app.documents import routers

    assert callable(getattr(routers, "no_store", None))

    async def next_response(_: Request) -> Response:
        return Response(headers={"Cache-Control": "public, max-age=60"})

    request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
    response = asyncio.run(routers.no_store(request, next_response))
    assert response.headers["cache-control"] == (
        "private, no-store" if private else "public, max-age=60"
    )


def test_bundled_thumbnail_stays_publicly_cached(world: Any) -> None:
    answer = world.api.get("/api/document-templates/classic/thumbnail?kind=cv", token=world.token)
    assert answer.status_code == 200
    assert answer.content.startswith(b"\x89PNG")
    assert answer.headers["cache-control"].startswith("public, max-age=")


def test_retry_busy_is_a_fixed_private_409(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(*_: Any, **__: Any) -> None:
        raise store.DocumentBusyError("synthetic lock context")

    monkeypatch.setattr(store, "lock_document", busy)
    answer = world.api.post(world.url, token=world.token)
    assert answer.status_code == 409
    assert answer.json() == {"detail": store.BUSY}
    assert answer.headers["cache-control"] == "private, no-store"
    assert captures(world) == []


def test_queue_exception_context_is_absent_from_full_records(
    world: Any, queue: Any, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()

    async def down(*_: Any, **__: Any) -> None:
        raise queues.QueueUnavailableError(planted)

    queue.enqueue = down
    caplog.set_level(logging.INFO)
    generation = response_generation(world, world.api.post(world.url, token=world.token))
    with world.factory() as db:
        assert db.get(AgentRun, generation.run_id).status is RunStatus.queued
    records = [vars(record) for record in caplog.records]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert planted not in json.dumps(records, default=str)
    assert queue.jobs == []


def test_concurrent_untrusted_bodies_cannot_choose_a_run_or_its_inputs(
    world: Any, queue: Any
) -> None:
    planted = marker()
    ready = Barrier(2)
    body = {
        "user_id": str(uuid.uuid4()),
        "document_id": str(uuid.uuid4()),
        "source_document_id": str(uuid.uuid4()),
        "model": planted,
        "script": planted,
        "source": "docx",
        "token_budget": 0,
    }

    def click() -> int:
        client = TestClient(world.app)
        try:
            ready.wait()
            return Api(client).post(world.url, token=world.token, json=body).status_code
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: click(), range(2)))
    assert sorted(answers) == [202, 409]
    made = captures(world)
    assert len(made) == 1
    assert made[0].user_id == world.user
    assert made[0].document_id == world.base
    with world.factory() as db:
        run = db.get(AgentRun, made[0].run_id)
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 1
        assert run.user_id == world.user
        assert run.input["generation_id"] == str(made[0].id)
        assert run.input["source"] == "pdf"
        assert run.input["token_budget"] == made[0].token_ceiling > 0
        assert planted not in json.dumps(run.input)
    assert len(queue.jobs) == 1
    assert queue.jobs[0]["args"] == (str(made[0].run_id),)
