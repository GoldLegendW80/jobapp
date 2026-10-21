"""The live answer end to end on the server (issue #33): real Postgres, real Redis, the real
worker on the fake backend, and GET /api/agents/runs/{id}/stream read by a stand-in browser.
The answer is seen partial before it is complete, a reconnect in the middle of it ends with the
full text exactly once, two tabs see the same thing, and what a dead worker or a dead Redis
leaves behind never shows."""

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis as syncredis
import redis.asyncio as aioredis
import uvicorn
from arq.worker import Worker
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import queue as queues
from app import worker
from app.agents import definitions, fake_backend, live, runs
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from app.identity.deps import COOKIE_NAME
from app.main import create_app
from tests.agents.conftest import ALICE
from tests.agents.live_helpers import Browser, Parsed, parse
from tests.agents.run_helpers import Backends, new_run, now, run_row, save_credential
from tests.conftest import free_port, make_settings
from tests.identity.api_helpers import CSRF, PASSWORD, make_user, session_cookie
from tests.jobs.test_worker import flush_redis, redis_url, serve_until, wait_for

__all__ = ["redis_url"]

ANSWER = "A long answer, é日\U0001f469‍\U0001f4bb, written slowly. " * 12


@pytest.fixture
def settings(migrated_url: str, redis_url: str, key: str, tmp_path: Path) -> Settings:
    flush_redis(redis_url)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(
        migrated_url,
        redis_url=redis_url,
        credentials_key=key,
        agents_fake_backend=True,
        agents_scratch_dir=str(scratch),
    )


@pytest.fixture
def streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The recorded streams plus `slowtext`: one answer written in small pieces."""
    folder = tmp_path / "streams"
    folder.mkdir()
    for path in fake_backend.STREAMS.glob("*.json"):
        (folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    init = {
        "type": "system",
        "subtype": "init",
        "session_id": "{session_id}",
        "uuid": str(uuid.uuid4()),
        "model": "{model}",
        "tools": [],
    }
    answer = {
        "type": "assistant",
        "session_id": "{session_id}",
        "uuid": str(uuid.uuid4()),
        "parent_tool_use_id": None,
        "message": {
            "id": "msg_slow",
            "type": "message",
            "role": "assistant",
            "model": "{model}",
            "content": [
                {"type": "thinking", "thinking": "SECRET-THOUGHT", "signature": "s"},
                {"type": "text", "text": ANSWER},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }
    body = {
        "partial": {"chars": 9, "sleep": 0.03},
        "steps": [{"message": init}, {"message": answer}],
    }
    (folder / "slowtext.json").write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", folder)
    return folder


class Stage:
    """The api served by a real uvicorn on a local port: a client that drops its connection is
    seen by the server at once, as a browser's is (Starlette's TestClient keeps a dropped stream
    running to its end, which would hide every reconnect in the middle of an answer)."""

    def __init__(self, settings: Settings, factory: sessionmaker[Session], key: str) -> None:
        self.settings = settings
        self.factory = factory
        self.app: FastAPI = create_app(settings)
        self.app.state.sse_settings = {
            "interval_s": 0.05,
            "transient_interval_s": 0.02,
            "heartbeat_s": 5,
            "max_s": 30,
        }
        self.user = make_user(factory, email=ALICE, name="Alice").id
        save_credential(factory, Keyring.from_text(key), self.user)
        port = free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                ProxyHeadersMiddleware(self.app, trusted_hosts="*"),
                host="127.0.0.1",
                port=port,
                log_config=None,
                ws="none",
                timeout_graceful_shutdown=1,
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "the api did not start"
            time.sleep(0.01)
        self.base = f"http://127.0.0.1:{port}"
        self.token = self.login(ALICE)

    def login(self, email: str) -> str:
        response = httpx.post(
            f"{self.base}/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF
        )
        assert response.status_code == 200, response.text
        morsel = session_cookie(response)
        assert morsel is not None
        return morsel.value

    def close(self) -> None:
        self.server.should_exit = True
        # A server still running past here leaks its sockets into whichever test runs next.
        self.thread.join(timeout=120)
        assert not self.thread.is_alive(), "the api did not stop"

    def run(self, script: str = "slowtext") -> uuid.UUID:
        return new_run(self.factory, self.user, {"message": "x", "script": script})

    def read(
        self,
        run_id: uuid.UUID,
        browser: Browser,
        stop_after: int | None = None,
        token: str | None = None,
    ) -> list[Parsed]:
        """One connection of `browser`, resumed from its Last-Event-ID, until the stream ends or
        `stop_after` events were taken (the client then drops the connection)."""
        seen: list[Parsed] = []
        headers = {"Cookie": f"{COOKIE_NAME}={token or self.token}"}
        if browser.last_id:
            headers["Last-Event-ID"] = str(browser.last_id)
        url = f"{self.base}/api/agents/runs/{run_id}/stream"
        with httpx.stream("GET", url, headers=headers, timeout=40) as response:
            assert response.status_code == 200, response.read()
            block: list[str] = []
            for line in response.iter_lines():
                if line:
                    block.append(line)
                    continue
                for event in parse(["\n".join(block).encode()]):
                    seen.append(event)
                    if event[0] in ("end", "gone"):
                        return seen
                    browser.take(event)
                    if stop_after is not None and len(seen) >= stop_after:
                        return seen
                block = []
        return seen

    def follow(self, run_id: uuid.UUID, browser: Browser, cuts: list[int]) -> None:
        for cut in cuts:
            found = self.read(run_id, browser, cut)
            if found and found[-1][0] == "end":
                return
        self.read(run_id, browser)


@pytest.fixture
def stage(settings: Settings, factory: sessionmaker[Session], key: str) -> Iterator[Stage]:
    made = Stage(settings, factory, key)
    yield made
    made.close()


def worker_context(settings: Settings, speed: float = 1.0) -> dict[str, Any]:
    ctx = worker.make_context(settings)
    ctx["agent_backend"] = Backends(speed=speed)
    ctx["agent_cancel_poll_s"] = 0.05
    return ctx


async def enqueue(workers: list[Worker], run_id: uuid.UUID) -> None:
    agents = next(w for w in workers if w.queue_name == queues.AGENTS)
    await agents.pool.enqueue_job(
        "run_agent", str(run_id), _job_id=queues.agent_run_job_id(run_id), _queue_name=queues.AGENTS
    )


def redis_keys(url: str) -> list[str]:
    async def go() -> list[str]:
        async with aioredis.from_url(url) as client:
            return sorted(key.decode() for key in await client.keys(live.KEY + "*"))

    return asyncio.run(go())


def assert_whole(browser: Browser, run_id: uuid.UUID, factory: sessionmaker[Session]) -> None:
    with factory() as db:
        rows = runs.events_after(db, run_id, 0, 500)
    assert [step["seq"] for step in browser.steps] == [row.seq for row in rows]
    assert browser.texts() == [ANSWER]
    assert browser.live is None
    assert browser.ended


def test_the_answer_is_partial_in_the_stream_before_it_is_complete(
    stage: Stage, streams: Path, settings: Settings, redis_url: str
) -> None:
    run_id = stage.run()
    browser = Browser()
    raw: list[Parsed] = []

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        raw.extend(await asyncio.to_thread(stage.read, run_id, browser))

    serve_until(settings, worker_context(settings), scenario)
    assert_whole(browser, run_id, stage.factory)
    partials = [data for name, _, data in raw if name == "partial"]
    shown = [text for _, text in browser.shown]
    # Intermediate states, not only the final one: many strict beginnings of the answer, growing.
    assert len({text for text in shown if text and text != ANSWER}) >= 5
    assert all(ANSWER.startswith(text) for text in shown)
    assert shown == sorted(shown, key=len)
    # A partial never carries an id, and its block starts after the step stored before it.
    assert all(event_id is None for name, event_id, _ in raw if name == "partial")
    names = [name for name, _, _ in raw]
    text_at = next(i for i, (name, _, data) in enumerate(raw) if data.get("kind") == "text")
    assert "partial" in names[:text_at]
    assert "partial" not in names[text_at:]  # nothing live once the complete text went out
    assert {data["after"] for data in partials} == {4}  # queued, running, init, thinking
    # Thinking was in the stream the worker read, and nowhere in what the browser got.
    assert "SECRET-THOUGHT" in (streams / "slowtext.json").read_text(encoding="utf-8")
    assert "SECRET-THOUGHT" not in json.dumps(raw)
    assert run_row(stage.factory, run_id).output == {"text": ANSWER}
    assert redis_keys(redis_url) == []  # nothing live is left in Redis


@pytest.mark.parametrize("cuts", [[3], [5], [6, 1, 1], [8, 2], [12], [4, 4, 4, 4, 4, 4]])
def test_reconnecting_in_the_middle_of_the_answer_ends_with_the_full_text_once(
    cuts: list[int], stage: Stage, streams: Path, settings: Settings
) -> None:
    run_id = stage.run()
    browser = Browser()

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        await asyncio.to_thread(stage.follow, run_id, browser, cuts)

    serve_until(settings, worker_context(settings), scenario)
    assert_whole(browser, run_id, stage.factory)
    assert all(ANSWER.startswith(text) for _, text in browser.shown)
    assert any(text and text != ANSWER for _, text in browser.shown)


def test_two_tabs_on_the_same_run_one_of_them_late(
    stage: Stage, streams: Path, settings: Settings
) -> None:
    run_id = stage.run()
    first, late = Browser(), Browser()

    def join_late() -> None:
        # Opens once the answer is well under way: it gets the text so far in one piece.
        while not any(len(text) > 100 for _, text in first.shown):
            time.sleep(0.01)
        stage.read(run_id, late)

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        await asyncio.gather(
            asyncio.to_thread(stage.read, run_id, first), asyncio.to_thread(join_late)
        )

    serve_until(settings, worker_context(settings), scenario)
    for browser in (first, late):
        assert_whole(browser, run_id, stage.factory)
        assert all(ANSWER.startswith(text) for _, text in browser.shown)
    assert len(late.shown[0][1]) > 100  # the late tab started from everything written so far
    assert len(late.shown) < len(first.shown)


def write_stale(url: str, run_id: uuid.UUID, after: int, text: str = "stale live text") -> None:
    async def go() -> None:
        async with aioredis.from_url(url) as client:
            store = live.RedisLiveStore(lambda: client)
            await store.open(run_id, 1, after, None)
            await store.append(run_id, 1, text)

    asyncio.run(go())


def test_a_worker_restart_in_the_middle_of_an_answer_leaves_no_live_text(
    stage: Stage, settings: Settings, redis_url: str
) -> None:
    """Orphan recovery: the worker died mid block (its buffer is still in Redis), the next worker
    start fails the run. A browser that was showing the text drops it; one that opens the run
    afterwards never sees it."""
    run_id = stage.run()
    with stage.factory() as db, db.begin():
        runs.claim(db, run_id, now())
        runs.append_event(db, run_id, 2, "status", {"status": "running"}, now())
    write_stale(redis_url, run_id, after=2)
    watching = Browser()
    found = stage.read(run_id, watching, stop_after=3)
    assert [name for name, _, _ in found] == ["step", "step", "partial"]
    assert watching.live_text() == "stale live text"

    async def restart(_: list[Worker]) -> None:
        await wait_for(lambda: run_row(stage.factory, run_id).status is RunStatus.failed)

    serve_until(settings, worker_context(settings), restart)
    stage.read(run_id, watching)
    assert watching.ended
    assert watching.live is None
    assert watching.steps[-1]["data"] == {"status": "failed", "error": runs.INTERRUPTED}
    assert watching.texts() == []
    afterwards = Browser()
    found = stage.read(run_id, afterwards)
    assert [name for name, _, _ in found] == ["step", "step", "step", "end"]
    assert afterwards.shown == []
    assert redis_keys(redis_url) != []  # the dead worker's buffer: it expires by itself
    assert live.TTL_S <= 30 * 60


def test_a_delta_after_the_complete_message_is_not_sent(stage: Stage, redis_url: str) -> None:
    """The window between the `text` step being stored and the worker clearing its buffer: the
    buffer still says the block is live, and the stream does not relay it."""
    run_id = stage.run()
    with stage.factory() as db, db.begin():
        runs.claim(db, run_id, now())
        runs.append_event(db, run_id, 2, "status", {"status": "running"}, now())
        runs.append_event(db, run_id, 3, "text", {"text": "complete"}, now())
    write_stale(redis_url, run_id, after=2, text="comp")

    def end() -> None:
        # The run goes on for a while with the stale buffer in place, then ends.
        time.sleep(0.5)
        with stage.factory() as db, db.begin():
            runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
            runs.append_event(db, run_id, 4, "status", {"status": "done"}, now())

    thread = threading.Thread(target=end)
    thread.start()
    try:
        browser = Browser()
        found = stage.read(run_id, browser)
    finally:
        thread.join()
    assert [name for name, _, _ in found] == ["step", "step", "step", "step", "end"]
    assert browser.shown == []
    # A client that resumes after the text step and is sent the stale block ignores it itself.
    resumed = Browser(steps=list(browser.steps[:3]))
    resumed.partial({"block": 1, "after": 2, "offset": 0, "text": "comp"})
    assert resumed.live is None


class PatientFeed(live.LiveFeed):
    """A feed whose back off outlasts any test: how often it logs no longer depends on how long a
    loaded machine takes to end the run (each 5 s of a slow stream added a line, issue #104)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **{**kwargs, "backoff_s": 3600})


def test_redis_down_the_stream_still_delivers_every_step(
    migrated_url: str,
    key: str,
    factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live, "LiveFeed", PatientFeed)
    settings = make_settings(
        migrated_url, redis_url="redis://127.0.0.1:1/0", credentials_key=key
    )  # nothing listens there
    stage = Stage(settings, factory, key)
    try:
        run_id = stage.run()
        with stage.factory() as db, db.begin():
            runs.claim(db, run_id, now())
            runs.append_event(db, run_id, 2, "text", {"text": "the answer"}, now())
            runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
            runs.append_event(db, run_id, 3, "status", {"status": "done"}, now())
        browser = Browser()
        found = stage.read(run_id, browser)
        assert [name for name, _, _ in found] == ["step", "step", "step", "end"]
        # A run still running: the stream stays up without Redis, the steps keep coming.
        running = stage.run()
        with stage.factory() as db, db.begin():
            runs.claim(db, running, now())
        found = stage.read(running, Browser(), stop_after=1)
        assert [name for name, _, _ in found] == ["step"]

        def later() -> None:
            time.sleep(0.3)
            with stage.factory() as db, db.begin():
                runs.append_event(db, running, 2, "text", {"text": "late"}, now())
                runs.finish(db, running, runs.Outcome(status=RunStatus.done), now())
                runs.append_event(db, running, 3, "status", {"status": "done"}, now())

        thread = threading.Thread(target=later)
        thread.start()
        try:
            patient = Browser()
            found = stage.read(running, patient)
        finally:
            thread.join()
        assert patient.texts() == ["late"]
        assert "partial" not in [name for name, _, _ in found]
        logged = capsys.readouterr().out
        assert "live text not read" in logged
        # It backs off, it does not hammer: once per connection that found the run running.
        assert logged.count("live text not read") <= 2
    finally:
        stage.close()


def test_with_the_setting_off_nothing_is_live(
    migrated_url: str, redis_url: str, key: str, factory: sessionmaker[Session]
) -> None:
    flush_redis(redis_url)
    settings = make_settings(
        migrated_url, redis_url=redis_url, credentials_key=key, agents_live_text=False
    )
    stage = Stage(settings, factory, key)
    try:
        assert stage.app.state.live_store is None
        run_id = stage.run()
        with stage.factory() as db, db.begin():
            runs.claim(db, run_id, now())
        write_stale(redis_url, run_id, after=1)
        found = stage.read(run_id, Browser(), stop_after=1)
        assert [name for name, _, _ in found] == ["step"]

        def end() -> None:
            time.sleep(0.3)
            with stage.factory() as db, db.begin():
                runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
                runs.append_event(db, run_id, 2, "status", {"status": "done"}, now())

        thread = threading.Thread(target=end)
        thread.start()
        try:
            found = stage.read(run_id, Browser())
        finally:
            thread.join()
        assert [name for name, _, _ in found] == ["step", "step", "end"]
    finally:
        stage.close()


def test_another_users_stream_never_reads_the_live_text(
    stage: Stage, factory: sessionmaker[Session], redis_url: str
) -> None:
    from tests.agents.conftest import BOB

    run_id = stage.run()
    with stage.factory() as db, db.begin():
        runs.claim(db, run_id, now())
    write_stale(redis_url, run_id, after=1, text="Alice's answer")
    make_user(factory, email=BOB, name="Bob")
    response = httpx.get(
        f"{stage.base}/api/agents/runs/{run_id}/stream",
        headers={"Cookie": f"{COOKIE_NAME}={stage.login(BOB)}"},
    )
    assert response.status_code == 404
    assert "Alice" not in response.text


# --- the Redis store itself ---------------------------------------------------------------------


def test_redis_store_appends_reads_by_offset_and_clears(redis_url: str) -> None:
    flush_redis(redis_url)
    run_id = uuid.uuid4()

    async def go() -> None:
        async with aioredis.from_url(redis_url) as client:

            async def pool() -> Any:  # the api's pool is awaited, the worker's is not
                return client

            store, reader = live.RedisLiveStore(lambda: client), live.RedisLiveStore(pool)
            assert await reader.read(run_id, None, 0) is None
            await store.open(run_id, 1, 7, None)
            assert await reader.read(run_id, None, 0) == live.Chunk(1, 7, b"", True)
            await store.append(run_id, 1, "é日")
            await store.append(run_id, 1, "\U0001f600!")
            whole = "é日\U0001f600!".encode()
            assert await reader.read(run_id, None, 0) == live.Chunk(1, 7, whole, True)
            assert await reader.read(run_id, 1, 5) == live.Chunk(1, 7, whole[5:], False)
            assert await reader.read(run_id, 1, len(whole)) == live.Chunk(1, 7, b"", False)
            # A reader that was on another block starts this one from its beginning.
            assert await reader.read(run_id, 4, 3) == live.Chunk(1, 7, whole, True)
            # Buffers expire by themselves when nothing clears them.
            for key in (store.meta_key(run_id), store.text_key(run_id, 1)):
                assert 0 < await client.ttl(key) <= live.TTL_S
            # The next block replaces the first: its text is gone.
            await store.open(run_id, 2, 9, 1)
            assert await client.exists(store.text_key(run_id, 1)) == 0
            assert await reader.read(run_id, 1, 5) == live.Chunk(2, 9, b"", True)
            await store.append(run_id, 2, "two")
            await store.clear(run_id, 2)
            assert await client.keys(live.KEY + "*") == []
            await store.clear(run_id, None)  # nothing to clear: fine
            # A meta that is not what this code wrote reads as "nothing live".
            await client.set(store.meta_key(run_id), b"not json")
            assert await reader.read(run_id, None, 0) is None
            await client.set(store.meta_key(run_id), b'{"block": "x"}')
            assert await reader.read(run_id, None, 0) is None

    asyncio.run(go())


# --- what Redis is asked, seen by Redis itself (issue #80) --------------------------------------


class Watch:
    """Every command the real Redis receives that names `needle`, in order, as Redis reports
    them (MONITOR). A run's live keys hold its id, so `needle` is the run's id: the list is
    everything, writes and reads, that any process asked Redis about that run's live text."""

    def __init__(self, url: str, needle: str) -> None:
        self.url = url
        self.needle = needle
        self.stop = f"stop-{uuid.uuid4()}"
        self.commands: list[str] = []
        self.listening = threading.Event()
        self.thread = threading.Thread(target=self._listen, daemon=True)

    def _listen(self) -> None:
        client = syncredis.Redis.from_url(self.url)
        try:
            with client.monitor() as monitor:
                self.listening.set()
                for seen in monitor.listen():
                    command = str(seen["command"])
                    if self.stop in command:
                        return
                    if self.needle in command:
                        self.commands.append(command)
        finally:
            client.close()

    def __enter__(self) -> "Watch":
        self.thread.start()
        assert self.listening.wait(10), "MONITOR did not start"
        return self

    def __exit__(self, *_: object) -> None:
        client = syncredis.Redis.from_url(self.url)
        try:
            client.echo(self.stop)
        finally:
            client.close()
        self.thread.join(timeout=10)
        assert not self.thread.is_alive()

    def named(self, verb: str) -> list[str]:
        return [c for c in self.commands if c.upper().startswith(verb.upper() + " ")]


def test_the_watch_sees_reads_and_writes(redis_url: str) -> None:
    """The instrument itself: a write and a read of a live key are both reported."""
    run_id = uuid.uuid4()

    async def touch() -> None:
        async with aioredis.from_url(redis_url) as client:
            store = live.RedisLiveStore(lambda: client)
            await store.open(run_id, 1, 0, None)
            await store.append(run_id, 1, "x")
            await store.read(run_id, None, 0)
            await store.clear(run_id, 1)

    with Watch(redis_url, str(run_id)) as watch:
        asyncio.run(touch())
    assert watch.named("set")
    assert watch.named("append")
    assert watch.named("get")
    assert watch.named("getrange")
    assert watch.named("del")


@pytest.mark.parametrize("live_text", [True, False], ids=["live", "live text off"])
def test_an_agent_with_live_text_off_never_touches_redis_for_its_run(
    live_text: bool,
    stage: Stage,
    streams: Path,
    settings: Settings,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whole run through the real worker and the real stream. With `live_text=False` on the
    agent's definition Redis never receives a command naming the run's live keys: no write by
    the worker (not even a clear), no read by the stream. The same run with the default
    definition is the control: there the keys are written and read."""
    monkeypatch.setitem(definitions.AGENTS, "echo", replace(definitions.ECHO, live_text=live_text))
    run_id = stage.run()
    browser = Browser()
    raw: list[Parsed] = []

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        raw.extend(await asyncio.to_thread(stage.read, run_id, browser))

    with Watch(redis_url, str(run_id)) as watch:
        # The arq job of the run is named after it too: only the live keys are looked at.
        serve_until(settings, worker_context(settings), scenario)
    touched = [c for c in watch.commands if live.KEY in c]
    assert_whole(browser, run_id, stage.factory)  # the answer arrived either way
    assert run_row(stage.factory, run_id).output == {"text": ANSWER}
    names = [name for name, _, _ in raw]
    if live_text:
        assert "partial" in names
        assert any(c.upper().startswith("APPEND ") for c in touched)
        assert any(c.upper().startswith("GET ") for c in touched)
    else:
        assert touched == []
        assert "partial" not in names
        assert browser.shown == []
    assert redis_keys(redis_url) == []


def listen(stage: Stage, run_id: uuid.UUID, seconds: float) -> list[Parsed]:
    """Stay on the run's stream until it ends or has been silent for `seconds`."""
    seen: list[Parsed] = []
    url = f"{stage.base}/api/agents/runs/{run_id}/stream"
    headers = {"Cookie": f"{COOKIE_NAME}={stage.token}"}
    timeout = httpx.Timeout(10, read=seconds)
    try:
        with httpx.stream("GET", url, headers=headers, timeout=timeout) as response:
            assert response.status_code == 200
            block: list[str] = []
            for line in response.iter_lines():
                if line:
                    block.append(line)
                    continue
                seen.extend(parse(["\n".join(block).encode()]))
                block = []
    except httpx.ReadTimeout:
        pass
    return seen


def listen_while_polled(stage: Stage, run_id: uuid.UUID, watch: Watch) -> tuple[list[Parsed], int]:
    """One connection to the stream of a running run, and how many times Redis was asked for
    its live text meanwhile. Reading again and again within ONE connection is what is looked
    for (a single read at the connection would not show the text as it is written), and it is
    waited for, not timed: a machine too loaded to poll three times in a window gets another
    window, up to a deadline no polling stream misses. How often the stream polls is a setting
    (`transient_interval_s`), which no assertion here turns into a rate."""
    deadline = time.monotonic() + 60
    while True:
        before = len(watch.named("get"))
        found = listen(stage, run_id, 1.2)
        reads = len(watch.named("get")) - before
        if reads >= 3 or time.monotonic() > deadline:
            return found, reads


def test_the_stream_reads_redis_only_while_the_run_is_running(stage: Stage, redis_url: str) -> None:
    """Queued, running without a block, running with one, ended: what the stream asks Redis in
    each state, counted by Redis. The stage polls its transient source every 20 ms. Not at all
    before the run is claimed, again and again while it runs, not at all once it has ended:
    the two zeros are exact, the count in between is not a rate."""
    run_id = stage.run()
    key = live.RedisLiveStore.meta_key(run_id)
    # Queued: a buffer under the run's key (left by anything) is not even looked at.
    write_stale(redis_url, run_id, after=0, text="not for a queued run")
    with Watch(redis_url, key) as queued:
        found = listen(stage, run_id, 1.2)
    assert [name for name, _, _ in found] == ["step"]
    assert queued.commands == []
    flush_redis(redis_url)
    # Running, no block being written yet: read at the stream's cadence, so that the first
    # characters of a block are not late.
    with stage.factory() as db, db.begin():
        runs.claim(db, run_id, now())
    with Watch(redis_url, key) as idle:
        found, reads = listen_while_polled(stage, run_id, idle)
    assert "partial" not in [name for name, _, _ in found]
    assert reads >= 3
    # Running, a block being written.
    write_stale(redis_url, run_id, after=1, text="being written")
    with Watch(redis_url, key) as writing:
        found, reads = listen_while_polled(stage, run_id, writing)
    assert [data["text"] for name, _, data in found if name == "partial"] == ["being written"]
    assert reads >= 3
    # Ended: the stream closes without a look at Redis.
    with stage.factory() as db, db.begin():
        runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
        runs.append_event(db, run_id, 2, "status", {"status": "done"}, now())
    with Watch(redis_url, key) as ended:
        found = listen(stage, run_id, 1.2)
    assert [name for name, _, _ in found][-1] == "end"
    assert ended.commands == []
