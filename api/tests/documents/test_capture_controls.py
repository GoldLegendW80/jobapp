"""606 A2: retry HTTP, early private cache boundary, concurrency and retry sinks."""

from __future__ import annotations

import io
import json
import logging
import socket
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
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
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
)
from app.ingestion.models import IngestionDocument
from app.logging_config import SafeStreamHandler
from tests.documents.canary import find, marker
from tests.documents.capture_support import captures, world
from tests.documents.test_base_api import alex, queue
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["alex", "api", "app", "clock", "engine", "factory", "migrated_url", "queue", "world"]
BASES = "/api/documents/base-cvs"


@contextmanager
def served(app: FastAPI) -> Iterator[httpx.Client]:
    """A client of the app behind a real uvicorn on a loopback socket, as the image runs it."""
    # Already listening: a request waits in the backlog until the server accepts it.
    listener = socket.create_server(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        port = listener.getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        listener.close()
    assert not thread.is_alive()


def file_gone(world: Any, how: str) -> None:
    """The original's bytes dropped by ingestion, or the ingestion document deleted."""
    if how == "bytes":
        with world.factory() as db:
            db.execute(
                sa.text("UPDATE ingestion_documents SET bytes=NULL WHERE id=:i"),
                {"i": world.upload},
            )
            db.commit()
        return
    deleted = world.api.request(
        "DELETE", f"/api/ingestion/documents/{world.upload}", token=world.token
    )
    assert deleted.status_code == 204, deleted.text
    with world.factory() as db:
        document = db.get(Document, world.base)
        assert (document.base_source, document.source_document_id) == (BaseSource.uploaded, None)


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


@pytest.mark.parametrize("how", ["bytes", "original"])
def test_clicks_while_the_file_is_gone_answer_one_failed_capture(
    world: Any, queue: Any, how: str
) -> None:
    file_gone(world, how)
    answers = [
        response_generation(world, world.api.post(world.url, token=world.token)) for _ in range(3)
    ]
    (made,) = captures(world)
    assert [answer.id for answer in answers] == [made.id] * 3
    assert (made.status, made.error, made.run_id) == (GenerationStatus.failed, "binding_gone", None)
    with world.factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0
    assert queue.jobs == []


def test_a_file_given_back_is_captured_by_the_next_click(world: Any, queue: Any) -> None:
    file_gone(world, "bytes")
    failed = response_generation(world, world.api.post(world.url, token=world.token))
    with world.factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET bytes=:b WHERE id=:i"),
            {"b": b"%PDF-1.7 invented scheduler fixture", "i": world.upload},
        )
        db.commit()
    started = response_generation(world, world.api.post(world.url, token=world.token))
    assert (failed.error, started.status) == ("binding_gone", GenerationStatus.running)
    assert {made.id for made in captures(world)} == {failed.id, started.id}
    assert len(queue.jobs) == 1


def test_an_open_generation_refuses_while_the_file_is_gone(world: Any) -> None:
    file_gone(world, "bytes")
    response_generation(world, world.api.post(world.url, token=world.token))
    with world.factory() as db:
        store.create_generation(
            db,
            world.user,
            world.base,
            GenerationMode.chat,
            GenerationTrigger.click,
            at=world.clock(),
            estimate={},
            token_ceiling=1,
        )
        db.commit()
    answer = world.api.post(world.url, token=world.token)
    assert answer.status_code == 409, answer.text
    assert len(captures(world)) == 1


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


def test_another_person_gets_the_missing_404_on_an_uploaded_cv(world: Any, queue: Any) -> None:
    make_user(world.factory, email="e2e-capture-stranger@example.org", name="Invented")
    stranger = world.api.login_token("e2e-capture-stranger@example.org")
    missing = world.api.post(f"/api/documents/{uuid.uuid4()}/design/capture", token=stranger)
    foreign = world.api.post(world.url, token=stranger)
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert (
        missing.headers.get("cache-control")
        == foreign.headers.get("cache-control")
        == "private, no-store"
    )
    assert captures(world) == []
    with world.factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0
    assert queue.jobs == []
    owned = response_generation(world, world.api.post(world.url, token=world.token))
    assert owned.status is GenerationStatus.running


def test_owned_non_upload_is_422(world: Any) -> None:
    with world.factory() as db:
        built = store.create_base(db, world.user, "Built", BaseSource.built, at=world.clock())
        db.commit()
    answer = world.api.post(f"/api/documents/{built.id}/design/capture", token=world.token)
    assert answer.status_code == 422, answer.text
    assert answer.json() == {"detail": "This CV was not converted from an uploaded file."}
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


@pytest.mark.parametrize(
    ("method", "target", "signed", "csrf", "expected"),
    [
        ("GET", "{id}", False, True, 401),
        ("GET", "not-a-uuid", True, True, 422),
        ("POST", "{id}/cancel", True, False, 403),
        ("POST", "{id}", True, True, 405),
    ],
)
def test_early_generation_refusals_are_private(
    world: Any, method: str, target: str, signed: bool, csrf: bool, expected: int
) -> None:
    path = "/api/document-generations/" + target.format(id=uuid.uuid4())
    token = world.token if signed else None
    answer = world.api.request(method, path, token=token, csrf=csrf)
    assert answer.status_code == expected, answer.text
    assert answer.headers.get("cache-control") == "private, no-store"


def test_unhandled_response_is_private_and_fixed(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("synthetic boundary failure")

    monkeypatch.setattr(store, "lock_document", broken)
    # Raises here what would leave the app, as uvicorn would then log it.
    client = TestClient(world.app, raise_server_exceptions=True)
    try:
        answer = Api(client).post(world.url, token=world.token)
    finally:
        client.close()
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


@pytest.mark.parametrize("prefix", ["/api/documents", "/api/document-generations"])
def test_an_unhandled_error_stays_out_of_the_server_log(
    world: Any, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str], prefix: str
) -> None:
    planted = marker()
    path = f"{prefix}/test-capture/error"

    def broken() -> None:
        raise RuntimeError(planted)

    world.app.add_api_route(path, broken, methods=["GET"])
    caplog.set_level(logging.INFO)
    # The app's own handler, as configured by create_app: what the container log receives.
    (handler,) = [one for one in logging.getLogger().handlers if isinstance(one, SafeStreamHandler)]
    written = io.StringIO()
    kept = handler.setStream(written)
    try:
        with served(world.app) as client:
            answer = Api(client).get(path, token=world.token)
    finally:
        handler.setStream(kept)
    output = capfd.readouterr()
    assert answer.status_code == 500
    assert answer.json() == {"detail": "internal server error"}
    assert answer.headers.get("cache-control") == "private, no-store"
    assert [one for one in caplog.records if one.name.startswith("uvicorn") and one.exc_info] == []
    logs = [*caplog.records, *written.getvalue().splitlines(), output.out, output.err]
    assert find([planted], logs=logs, values=[answer.text, dict(answer.headers)]) == []
    failed = [one for one in caplog.records if one.getMessage() == "documents.request.failed"]
    assert [(one.name, one.levelno, getattr(one, "error", None)) for one in failed] == [
        ("app.documents", logging.ERROR, "RuntimeError")
    ]
    assert "documents.request.failed" in written.getvalue()
    assert captures(world) == []


@pytest.mark.parametrize(
    ("path", "private"),
    [
        ("/api/documents", True),
        ("/api/documents/", True),
        ("/api/documents/anything/deeper", True),
        ("/api/documentsx", False),
        ("/api/document-generations", True),
        ("/api/document-generations/anything/cancel", True),
        ("/api/document-generationsx", False),
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

    async def failing(_: Request) -> Response:
        raise RuntimeError("synthetic boundary failure")

    request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
    response = asyncio.run(routers.no_store(request, next_response))
    assert response.headers["cache-control"] == (
        "private, no-store" if private else "public, max-age=60"
    )
    if not private:
        with pytest.raises(RuntimeError):
            asyncio.run(routers.no_store(request, failing))
        return
    failed = asyncio.run(routers.no_store(request, failing))
    assert (failed.status_code, failed.body) == (500, b'{"detail":"internal server error"}')
    assert failed.headers["cache-control"] == "private, no-store"


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
