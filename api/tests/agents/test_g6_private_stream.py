"""A private run end to end (ingestion G6, points 3 and 10): the real worker on the fake backend,
a real Postgres, a real Redis watched with MONITOR, and the run's stream read over HTTP by a
stand-in browser. With AGENTS_LIVE_TEXT on, Redis never receives a command that names the run's
live keys (no write by the worker, no read by the stream) nor one that carries a word of the
document, the arq result included; the same run of a public agent is the control, where the
instrument sees both. MARKER stands for the document."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import redis as syncredis
from arq.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import definitions, live
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    ALL_TOOLS,
    MARKER,
    Streams,
    agent,
    assistant,
    call,
    everything_stored,
    g6_tools,
    init,
    streams,
    text,
)
from tests.agents.live_helpers import Browser, Parsed
from tests.agents.run_helpers import new_run, run_row
from tests.agents.test_live_stream import Stage, Watch, enqueue, redis_keys, worker_context
from tests.conftest import make_settings
from tests.jobs.test_worker import flush_redis, redis_url, serve_until

__all__ = ["g6_tools", "redis_url", "streams"]

DOCUMENT = f"Camille Durand, engineer. {MARKER}. Started in 2021."
PROPOSED = f"Value copied from the document: {MARKER}"
SPOKEN = f"The document says: {MARKER}. " * 12


def bound(db: Session, run: Any) -> dict[str, Any]:
    return {"document_id": "doc-1", "text": DOCUMENT}


PRIVATE = agent("g6_private", tools=ALL_TOOLS, private_io=True, bind=bound)
PUBLIC = agent("g6_private", tools=ALL_TOOLS, private_io=False, bind=bound)


@pytest.fixture
def settings(migrated_url: str, redis_url: str, key: str, tmp_path: Path) -> Settings:
    flush_redis(redis_url)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    found = make_settings(
        migrated_url,
        redis_url=redis_url,
        credentials_key=key,
        agents_fake_backend=True,
        agents_scratch_dir=str(scratch),
    )
    assert found.agents_live_text is True  # the setting is on: the definition decides
    return found


@pytest.fixture
def stage(settings: Settings, factory: sessionmaker[Session], key: str) -> Iterator[Stage]:
    made = Stage(settings, factory, key)
    yield made
    made.close()


def everything_in_redis(url: str) -> str:
    """Every key of this Redis database and what it holds, as text."""
    client = syncredis.Redis.from_url(url)
    try:
        found = []
        for key in client.scan_iter("*"):
            kind = client.type(key).decode()
            if kind == "string":
                value: Any = client.get(key)
            elif kind == "zset":
                value = client.zrange(key, 0, -1, withscores=True)
            elif kind == "list":
                value = client.lrange(key, 0, -1)
            elif kind == "hash":
                value = client.hgetall(key)
            else:
                value = client.smembers(key) if kind == "set" else kind
            found.append(f"{key!r} {value!r}")
        return "\n".join(found)
    finally:
        client.close()


def arq_result(url: str, run_id: uuid.UUID) -> Any:
    """What arq kept as the result of the run's job."""
    client = syncredis.Redis.from_url(url)
    try:
        raw = client.get("arq:result:" + queues.agent_run_job_id(run_id))
        assert raw is not None, "arq kept no result for the run's job"
        return queues.deserialize(raw)
    finally:
        client.close()


def play(
    stage: Stage, settings: Settings, redis_url: str, run_id: uuid.UUID
) -> tuple[list[Parsed], Browser, Watch, Watch]:
    """The run through the real worker while a browser follows its stream; everything Redis
    was asked about the run, and everything it was told that holds a word of the document."""
    browser = Browser()
    raw: list[Parsed] = []

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        raw.extend(await asyncio.to_thread(stage.read, run_id, browser))

    with Watch(redis_url, str(run_id)) as named, Watch(redis_url, MARKER) as leaked:
        serve_until(settings, worker_context(settings), scenario)
    return raw, browser, named, leaked


@pytest.mark.parametrize("private", [True, False], ids=["private", "public, the control"])
def test_redis_is_never_told_anything_of_a_private_run(
    private: bool,
    stage: Stage,
    streams: Streams,
    g6_tools: None,
    settings: Settings,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    found = PRIVATE if private else PUBLIC
    monkeypatch.setitem(definitions.AGENTS, found.name, found)  # the worker and the api know it
    streams.add(
        "g6_private",
        [
            init(),
            assistant("m1", text(SPOKEN), call("t1", "g6_read")),
            assistant("m2", call("t2", "g6_write", text=PROPOSED)),
            assistant("m3", text(f"Stored the item about {MARKER}.")),
        ],
        # Pieces long enough to hold the marker whole: a public run then writes it to Redis
        # in one command, which is what the second watch looks for.
        partial={"chars": 80, "sleep": 0.05},
    )
    run_id = new_run(stage.factory, stage.user, {}, agent="g6_private")
    raw, browser, named, leaked = play(stage, settings, redis_url, run_id)
    assert run_row(stage.factory, run_id).status is RunStatus.done
    assert browser.ended
    touched = [command for command in named.commands if live.KEY in command]
    names = [name for name, _, _ in raw]
    steps = {data["kind"]: data["data"] for name, _, data in raw if name == "step"}
    if private:
        # Not a write by the worker (not even a clear), not a read by the stream.
        assert touched == []
        assert "partial" not in names
        assert browser.shown == []
        # Not a word of the document in any command Redis received, whoever sent it.
        assert leaked.commands == []
        assert MARKER not in everything_in_redis(redis_url)
        # The stream carries what is stored: names and sizes, and the model's own words.
        assert set(steps["tool_call"]) == {"id", "name", "input_size"}
        assert set(steps["tool_result"]) == {"id", "is_error", "size", "blocks"}
        assert browser.texts() == [SPOKEN, f"Stored the item about {MARKER}."]
        row = run_row(stage.factory, run_id)
        assert row.output is not None
        assert all(type(value) is int for value in row.output.values())
        # The only place the marker is: the text events of the run (the model's words).
        stored = everything_stored(stage.factory, run_id)
        assert stored.count(MARKER) == SPOKEN.count(MARKER) + 1
    else:
        # The instrument sees a run that does publish: its text was written to Redis and read.
        assert any(command.upper().startswith("APPEND ") for command in touched)
        assert any(command.upper().startswith("GET ") for command in touched)
        assert "partial" in names
        assert leaked.commands != []
        assert "content" in steps["tool_result"]
    assert redis_keys(redis_url) == []  # nothing live is left either way
    assert arq_result(redis_url, run_id)["r"] == "done"  # a status word, whatever the agent
    assert MARKER not in json.dumps(arq_result(redis_url, run_id))


def test_an_agent_the_api_does_not_know_gets_no_feed_either(
    stage: Stage,
    streams: Streams,
    g6_tools: None,
    settings: Settings,
    redis_url: str,
) -> None:
    """The worker imported the agent's module and the api did not: the stream still reads
    nothing from Redis for the run (an agent it does not know has nothing live)."""
    streams.add("g6_private", [init(), assistant("m1", text(SPOKEN))])
    run_id = new_run(stage.factory, stage.user, {}, agent="g6_private")
    assert "g6_private" not in definitions.AGENTS
    browser = Browser()
    raw: list[Parsed] = []

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        raw.extend(await asyncio.to_thread(stage.read, run_id, browser))

    ctx = worker_context(settings)
    ctx["agent_definitions"] = {PRIVATE.name: PRIVATE}
    with Watch(redis_url, str(run_id)) as named, Watch(redis_url, MARKER) as leaked:
        serve_until(settings, ctx, scenario)
    assert run_row(stage.factory, run_id).status is RunStatus.done
    assert [command for command in named.commands if live.KEY in command] == []
    assert leaked.commands == []
    assert "partial" not in [name for name, _, _ in raw]


def test_a_private_run_that_fails_after_a_tool_call_leaves_nothing_anywhere(
    stage: Stage,
    streams: Streams,
    g6_tools: None,
    settings: Settings,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Claude Code dies in the middle of the work, through the real worker: nothing of the
    document in the run's events, its row, the worker's and the api's log, the live buffer or
    the arq result."""
    monkeypatch.setitem(definitions.AGENTS, PRIVATE.name, PRIVATE)
    streams.add(
        "g6_private",
        [
            init(),
            assistant("m1", text("Reading the document."), call("t1", "g6_read")),
            assistant("m2", call("t2", "g6_write", text=PROPOSED)),
            assistant("m3", call("t3", "g6_fail", text=PROPOSED)),
            {"raise": "process"},
        ],
    )
    run_id = new_run(stage.factory, stage.user, {}, agent="g6_private")
    with caplog.at_level("DEBUG"):
        raw, browser, named, leaked = play(stage, settings, redis_url, run_id)
    row = run_row(stage.factory, run_id)
    assert (row.status, row.error) == (
        RunStatus.failed,
        "The Claude Code process failed (exit code 1).",
    )
    assert row.output == {
        "turns": 0,
        "text_blocks": 1,
        "text_chars": len("Reading the document."),
        "tool_calls": 3,
        "tool_errors": 1,
        "tools_denied": 0,
    }
    assert browser.ended
    stored = everything_stored(stage.factory, run_id)
    assert MARKER not in stored
    assert "Camille" not in stored
    assert MARKER not in json.dumps(raw)  # nor in what the owner's own stream carried
    captured = capsys.readouterr()
    extras = " ".join(str(vars(record)) for record in caplog.records)
    assert MARKER not in caplog.text + captured.out + captured.err + extras
    assert [command for command in named.commands if live.KEY in command] == []
    assert leaked.commands == []
    assert MARKER not in everything_in_redis(redis_url)
    assert arq_result(redis_url, run_id)["r"] == "failed"
