"""The ingestion queue for real: the worker of app/worker.py on a real Redis and a real Postgres,
the api's own Queue, and a parser the test plays (the parser service itself is
tests/ingestion/parser/test_pdf_container.py).

An upload is read without anyone asking twice; two users' documents each get their own text; a
document deleted while the worker reads it gets none; a parser that is down is tried once more
by the reaper, then the document is failed; a PDF that was stored before PDFs were read is
read when the worker starts; images and archives are never touched; and Redis holds nothing of
a document, during or after.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis.asyncio as aioredis
from arq.worker import Worker
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import queue as queues
from app import worker
from app.config import Settings
from app.ingestion import jobs
from app.ingestion import parser_client as pc
from app.ingestion.models import FailedKind, TextStatus
from app.main import create_app
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, make_user
from tests.ingestion.conftest import ALICE, BOB
from tests.ingestion.helpers import delete, listing, pdf, png, row, rows, uploaded, user_id
from tests.ingestion.test_jobs import Parser, parsed, stored_before
from tests.jobs.test_worker import flush_redis, redis_url, serve_until, wait_for

__all__ = ["redis_url"]


@pytest.fixture
def settings(migrated_url: str, redis_url: str) -> Settings:
    flush_redis(redis_url)
    return make_settings(migrated_url, redis_url=redis_url)


@pytest.fixture
def served(settings: Settings, factory: sessionmaker[Session]) -> Iterator[tuple[Api, str, str]]:
    """The api with its real queue on the test's Redis, and two signed-in users."""
    with TestClient(ProxyHeadersMiddleware(create_app(settings), trusted_hosts="*")) as client:
        api = Api(client)
        make_user(factory, email=ALICE, name="alice")
        make_user(factory, email=BOB, name="bob")
        yield api, api.login_token(ALICE), api.login_token(BOB)


@pytest.fixture(autouse=True)
def a_tick_every_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reaper's cron every second instead of every fifteen, and no wait before a retry."""
    monkeypatch.setattr(jobs, "REAP_SECONDS", tuple(range(60)))
    monkeypatch.setattr(jobs, "RETRY_AFTER", timedelta(0))


def context(settings: Settings, parser: Parser) -> dict[str, Any]:
    ctx = worker.make_context(settings)
    ctx["ingestion_parse"] = parser
    return ctx


def status(factory: sessionmaker[Session], document_id: str) -> TextStatus:
    return row(factory, document_id).text_status


def redis_dump(url: str) -> str:
    """Every key of this worker's Redis database and every value under it, as one text."""

    async def dump() -> str:
        found = []
        async with aioredis.from_url(url) as client:
            for key in await client.keys("*"):
                kind = (await client.type(key)).decode()
                if kind == "string":
                    value: Any = await client.get(key)
                elif kind == "zset":
                    value = await client.zrange(key, 0, -1)
                elif kind == "list":
                    value = await client.lrange(key, 0, -1)
                elif kind == "hash":
                    value = await client.hgetall(key)
                else:
                    value = await client.smembers(key)
                found.append(f"{key!r}={value!r}")
        return "\n".join(found)

    return asyncio.run(dump())


def test_the_ingestion_queue_reads_one_document_at_a_time_and_keeps_no_result() -> None:
    spec = worker.ingestion_queue()
    assert (spec.name, spec.max_jobs) == ("jobapp:ingestion", 1)
    functions = {function.name: function for function in spec.functions}
    # `advance_ingestion_runs` is the tick of the chain (G10, test_engine_worker.py).
    assert set(functions) == {"parse_document", "reap_documents", "advance_ingestion_runs"}
    for function in functions.values():
        assert function.keep_result_s == 0
        assert function.max_tries == 1
    assert functions["parse_document"].timeout_s == jobs.JOB_TIMEOUT_S
    (cron,) = [one for one in spec.cron_jobs if one.name == "cron:reap_documents"]
    assert cron.run_at_startup is True
    assert cron.second == set(jobs.REAP_SECONDS)
    names = [queue.name for queue in worker.queues()]
    # the documents queue comes right after (the apply queue of A5 after it)
    assert names.index(queues.DOCUMENTS) == names.index(queues.INGESTION) + 1


def test_an_upload_is_read_by_the_worker_and_redis_holds_nothing_of_it(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session], redis_url: str
) -> None:
    api, alice, _ = served
    words = f"words-{uuid.uuid4().hex}"
    name = f"name-{uuid.uuid4().hex}"
    text = f"{words} page one\f{words} page two"
    body = uploaded(api, alice, pdf(900) + words.encode(), f"{name}.pdf")
    assert body["status"] == "pending"
    queued = redis_dump(redis_url)
    assert body["id"] in queued, "the job is in Redis, named by the id of its document"
    assert words not in queued
    assert name not in queued
    parser = Parser(parsed(text))
    during: list[str] = []
    parser.during = lambda: during.append(redis_dump(redis_url))

    async def scenario(_: list[Worker]) -> None:
        await wait_for(lambda: status(factory, body["id"]) is TextStatus.done)
        # One more second: the reaper ticks, and finds nothing to do.
        await asyncio.sleep(1.2)

    serve_until(settings, context(settings, parser), scenario)
    assert row(factory, body["id"]).text == text
    assert len(parser.calls) == 1
    after = redis_dump(redis_url)
    for dump in (*during, after):
        assert words not in dump
        assert name not in dump
    # No result is kept at all: not the word the job returned, not the id of the document.
    assert "arq:result" not in after
    assert body["id"] not in after
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["pages_text"]) == ("done", 2)


def test_the_documents_of_two_users_each_get_their_own_text(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session]
) -> None:
    api, alice, bob = served
    hers = uploaded(api, alice, pdf(1000) + b"alice", "cv.pdf")
    his = uploaded(api, bob, pdf(1000) + b"bob", "cv.pdf")

    class ByContent(Parser):
        async def __call__(self, kind: str, data: bytes, options: Any = None) -> pc.Parsed:
            if kind == jobs.RENDER_KIND:
                # The renderer of the scanned pages (G8) is asked after each short PDF; the
                # plain Parser declines every page, as the real one does for a page of text.
                return await super().__call__(kind, data, options)
            self.calls.append((kind, data))
            return parsed(f"the text of {data[-5:].decode().strip(chr(0))}")

    parser = ByContent()

    async def scenario(_: list[Worker]) -> None:
        await wait_for(
            lambda: {status(factory, hers["id"]), status(factory, his["id"])} == {TextStatus.done}
        )

    serve_until(settings, context(settings, parser), scenario)
    assert row(factory, hers["id"]).text == "the text of alice"
    assert row(factory, his["id"]).text == "the text of bob"
    assert [item["id"] for item in listing(api, alice)["items"]] == [hers["id"]]
    assert [item["id"] for item in listing(api, bob)["items"]] == [his["id"]]
    assert len(parser.calls) == 2


def test_a_document_deleted_while_the_worker_reads_it_gets_no_text(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session]
) -> None:
    api, alice, _ = served
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(parsed("a text for a document that is gone"))
    parser.during = lambda: delete(api, alice, body["id"]).raise_for_status()

    async def scenario(_: list[Worker]) -> None:
        await wait_for(lambda: bool(parser.calls) and rows(factory) == [])
        await asyncio.sleep(1.5)  # the write would have happened by now

    serve_until(settings, context(settings, parser), scenario)
    assert rows(factory) == []
    assert listing(api, alice)["usage"] == {"bytes": 0, "documents": 0}
    assert len(parser.calls) == 1


def test_a_parser_that_is_down_is_tried_once_more_by_the_reaper_then_the_document_fails(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session]
) -> None:
    api, alice, _ = served
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(pc.ParserDownError("unreachable"))

    async def scenario(_: list[Worker]) -> None:
        await wait_for(lambda: status(factory, body["id"]) is TextStatus.failed)
        await asyncio.sleep(2.2)  # two more ticks: nothing tries a third time

    serve_until(settings, context(settings, parser), scenario)
    stored = row(factory, body["id"])
    assert (stored.failed_kind, stored.failed_reason) == (FailedKind.server, jobs.SERVER_FAILED)
    assert (stored.attempts, stored.raw) == (jobs.MAX_ATTEMPTS, None)
    assert len(parser.calls) == jobs.MAX_ATTEMPTS == 2
    item = listing(api, alice)["items"][0]
    assert (item["status"], item["failed_reason"]) == ("failed", jobs.SERVER_FAILED)


def test_a_parser_that_comes_back_reads_the_document_at_the_second_try(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session]
) -> None:
    api, alice, _ = served
    body = uploaded(api, alice, pdf(), "cv.pdf")
    parser = Parser(pc.ParserBusyError("busy"), parsed("read at last"))

    async def scenario(_: list[Worker]) -> None:
        await wait_for(lambda: status(factory, body["id"]) is TextStatus.done)

    serve_until(settings, context(settings, parser), scenario)
    stored = row(factory, body["id"])
    assert (stored.text, stored.attempts, stored.failed_kind) == ("read at last", 2, None)


def test_what_was_stored_before_this_version_is_read_when_the_worker_starts(
    settings: Settings, served: tuple[Api, str, str], factory: sessionmaker[Session]
) -> None:
    """Rows as G1 left them, never queued: the PDF is read on the reaper's first tick, without
    a new upload; a row of a type no reader exists for still waits after ten ticks, bytes and
    all, and the parser never saw it. An archive has a reader since G7 and an image since G8
    (tests/ingestion/test_jobs_archive.py: the reaper queues both), so the waiting row here is of
    a type outside the readable ones."""
    api, alice, _ = served
    owner = user_id(factory, ALICE)
    weeks_ago = datetime.now(UTC).replace(microsecond=0) - timedelta(days=20)
    old_pdf = stored_before(factory, owner, pdf(1200), "application/pdf", weeks_ago)
    unread = stored_before(factory, owner, png(), "image/webp", weeks_ago)
    assert "image/webp" not in jobs.readable_mimes()
    parser = Parser(parsed("read at the deploy"))
    ticks = 0

    async def scenario(workers: list[Worker]) -> None:
        nonlocal ticks
        await wait_for(lambda: status(factory, str(old_pdf)) is TextStatus.done, within=10)
        ingestion = next(w for w in workers if w.queue_name == queues.INGESTION)
        started = ingestion.jobs_complete
        await wait_for(lambda: ingestion.jobs_complete - started >= 10, within=30)
        ticks = ingestion.jobs_complete - started

    serve_until(settings, context(settings, parser), scenario)
    assert ticks >= 10
    assert row(factory, str(old_pdf)).text == "read at the deploy"
    assert parser.calls == [("pdf_text", pdf(1200))]
    for document_id, data in ((unread, png()),):
        waiting = row(factory, str(document_id))
        assert (waiting.text_status, waiting.raw) == (TextStatus.pending, data)
        assert (waiting.attempts, waiting.failed_kind, waiting.failed_reason) == (0, None, None)
        assert waiting.pending_since == weeks_ago
    items = {item["id"]: item for item in listing(api, alice)["items"]}
    assert items[str(unread)]["readable"] is False
    assert (items[str(old_pdf)]["status"], items[str(old_pdf)]["readable"]) == ("done", True)


def test_the_health_check_counts_the_ingestion_queue(settings: Settings) -> None:
    parser = Parser(parsed())
    seen: list[int] = []

    async def scenario(_: list[Worker]) -> None:
        deadline = asyncio.get_running_loop().time() + 20
        while await worker.check(settings) != 0:
            assert asyncio.get_running_loop().time() < deadline, "no health record"
            await asyncio.sleep(0.2)
        seen.append(await worker.check(settings, [worker.ingestion_queue()]))

    serve_until(settings, context(settings, parser), scenario)
    assert seen == [0]
    assert asyncio.run(worker.check(settings, [worker.ingestion_queue()])) == 1
