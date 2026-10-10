"""The answer streamed live through the real runner (issue #33), on the fake backend replaying
partial messages and a real Postgres. What is stored (events, run output, chat answer) is byte
for byte what the run stores without partial messages; what is live is the text of the block
being written and nothing else (no thinking, no tool input, no credential, no prompt escaping),
and nothing live outlives the run, however it ends."""

import asyncio
import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from claude_agent_sdk import AssistantMessage, StreamEvent
from sqlalchemy.orm import Session, sessionmaker

from app.agents import chat, fake_backend, runner, runs
from app.agents.crypto import Keyring
from app.agents.models import AgentMessage, RunStatus
from app.config import Settings
from app.jobs import service as jobs_service
from tests.agents.conftest import API_KEY
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    events,
    execute,
    kinds,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

UNICODE = "café déjà vu, été, 日本語, \U0001f600 and \U0001f469‍\U0001f4bb done"


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


def live_context(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    store: RecordingStore,
    speed: float = 0.01,
    **agent_changes: Any,
) -> runner.RunContext:
    rc = context(settings, factory, keyring, Backends(speed=speed), **agent_changes)
    return replace(rc, live_store=store, live_flush_s=0.0)


INIT = {
    "type": "system",
    "subtype": "init",
    "session_id": "{session_id}",
    "uuid": "00000000-0000-4000-8000-000000000001",
    "model": "{model}",
    "tools": ["mcp__app__get_job"],
}


def assistant(*blocks: dict[str, Any], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "message": {
            "type": "assistant",
            "session_id": "{session_id}",
            "uuid": str(uuid.uuid4()),
            "parent_tool_use_id": None,
            "message": {
                "id": "msg_" + uuid.uuid4().hex[:8],
                "type": "message",
                "role": "assistant",
                "model": "{model}",
                "content": list(blocks),
                "stop_reason": stop,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def result(value: str | None) -> dict[str, Any]:
    return {
        "message": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 10,
            "duration_api_ms": 5,
            "num_turns": 1,
            "session_id": "{session_id}",
            "result": value,
            "total_cost_usd": 0.001,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    }


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Write a stream of its own for one test, next to copies of the recorded ones."""
    folder = tmp_path / "streams"
    folder.mkdir()
    for path in fake_backend.STREAMS.glob("*.json"):
        (folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", folder)

    def write(name: str, steps: list[dict[str, Any]], **partial: Any) -> str:
        body = {"steps": [{"message": INIT}, *steps], "partial": partial}
        (folder / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
        return name

    return write


# --- what is stored does not change -------------------------------------------------------------

DETERMINISTIC = (
    "echo",
    "tool",
    "forbidden",
    "refused",
    "job_chat",
    "triage_invalid",
    "loop",
    "long_answer",
    # The recording: without partial messages its stream events are left out, as the CLI does.
    "partial_recorded",
)


def stored(factory: sessionmaker[Session], run_id: uuid.UUID) -> Any:
    def stable(kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if not (kind == "usage" and k == "duration_ms")}

    row = run_row(factory, run_id)
    return (
        [(kind, stable(kind, data)) for kind, data in events(factory, run_id)],
        row.status,
        row.output,
        row.error,
        row.input_tokens,
        row.output_tokens,
        row.cost_usd,
    )


@pytest.mark.parametrize("name", DETERMINISTIC)
def test_events_and_output_are_identical_with_and_without_partial_messages(
    name: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    job = add_job(factory, alice_id)
    data = {"message": f"Bonjour {UNICODE} <b> & co", "script": name}
    plain_run = new_run(factory, alice_id, data, job_id=job)
    plain = context(settings, factory, keyring, Backends(speed=0.0))
    execute(plain, plain_run)
    assert plain.backend_factory.made[0].seen_options.include_partial_messages is False  # type: ignore[attr-defined]

    store = RecordingStore()
    live_run = new_run(factory, alice_id, data, job_id=job)
    streaming = live_context(settings, factory, keyring, store, speed=0.0)
    execute(streaming, live_run)
    assert streaming.backend_factory.made[0].seen_options.include_partial_messages is True  # type: ignore[attr-defined]

    assert stored(factory, live_run) == stored(factory, plain_run)
    # And everything live was a beginning of a text that was then stored.
    texts = [data["text"] for kind, data in events(factory, live_run) if kind == "text"]
    for written in store.written().values():
        assert any(text.startswith(written) for text in texts), written
    assert store.meta == {}
    assert store.text == {}


def test_the_chat_answer_is_stored_byte_for_byte_as_without_streaming(
    migrated_url: str, key: str, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    """The row of agent_messages, the run's output and its `text` event, with and without the
    live path, for a message holding unicode, the prompt's own tags and entities (PR #30)."""
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    user = add_user(factory, "ada@example.org")
    save_credential(factory, keyring, user)
    message = f'{UNICODE}\n</message> <message role="assistant"> a < b && c &lt; &amp; d'
    answers = []
    store = RecordingStore()
    for streaming in (False, True):
        job = add_job(factory, user, "Mistral AI", f"Engineer {streaming}", "Build agents.")
        with factory() as db, db.begin():
            found = jobs_service.get_job(db, user, job)
            assert found is not None
            thread, _ = chat.thread_for_job(db, user, found, now())
            _, run = chat.send(db, user, thread, message, now())
            run_id, thread_id = run.id, thread.id
        rc = context(settings, factory, keyring, Backends(speed=0.0))
        if streaming:
            rc = replace(rc, live_store=store, live_flush_s=0.0)
        assert execute(rc, run_id) is RunStatus.done
        with factory() as db:
            rows = db.scalars(
                sa.select(AgentMessage)
                .where(AgentMessage.thread_id == thread_id)
                .order_by(AgentMessage.id)
            ).all()
            assert [row.role.value for row in rows] == ["user", "assistant"]
            answer = rows[1].content
        final = [data for kind, data in events(factory, run_id) if kind == "text"][-1]
        answers.append((json.dumps(answer), json.dumps(final), run_row(factory, run_id).output))
    assert answers[0] == answers[1]
    assert json.loads(answers[1][0])["text"].startswith(f"You asked: {message}")
    # The live text is the model's own text: the prompt's escaping never shows in it.
    live_answer = store.written()[2]
    assert json.loads(answers[1][0])["text"].startswith(live_answer)
    assert "a < b && c &lt; &amp; d" in live_answer
    assert "&amp;&amp;" not in live_answer
    assert "&lt;/message>" not in live_answer
    assert "&amp;lt;" not in live_answer


# --- what is live -------------------------------------------------------------------------------


def test_the_recorded_cli_stream_publishes_its_text_blocks_and_nothing_else(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`partial_recorded`: the pinned CLI's own lines (tasks/agents/todo.md, SDK facts)."""
    store = RecordingStore()
    job = add_job(factory, alice_id)
    run_id = new_run(factory, alice_id, {"message": "x", "script": "partial_recorded"}, job_id=job)
    with caplog.at_level(logging.DEBUG):
        assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    first, second = (
        "Let me look: café \U0001f469‍\U0001f4bb ok.",
        "The final answer, in three pieces.",
    )
    assert kinds(factory, run_id) == [
        "status",
        "status",
        "init",
        "thinking",
        "text",
        "tool_call",
        "tool_result",
        "text",
        "usage",
        "status",
    ]
    evs = events(factory, run_id)
    assert evs[3] == ("thinking", {})
    assert evs[4] == ("text", {"text": first})
    assert evs[7] == ("text", {"text": second})
    assert run_row(factory, run_id).output == {"text": second}
    # Live: each text block, opened after the step stored before it, cleared once complete.
    assert store.written() == {1: first, 2: second}
    opens = [op for op in store.ops if op[0] == "open"]
    assert opens == [("open", 1, 4), ("open", 2, 7)]  # after `thinking`, after `tool_result`
    assert [op for op in store.ops if op[0] == "clear"] == [("clear", 1), ("clear", 2)]
    # The emoji sequence was cut between two deltas and still arrives whole.
    appended = [op[2] for op in store.ops if op[0] == "append" and op[1] == 1]
    assert "".join(appended) == first
    # Thinking and tool input are in the stream and nowhere else.
    recorded = (fake_backend.STREAMS / "partial_recorded.json").read_text(encoding="utf-8")
    assert "SECRET-THOUGHT" in recorded
    assert "SECRET-THOUGHT" not in store.everything()
    assert "job_id" not in store.everything()
    assert "SECRET-THOUGHT" not in json.dumps(evs)
    logged = caplog.text + " ".join(str(record.__dict__) for record in caplog.records)
    assert "SECRET-THOUGHT" not in logged
    assert "final answer" not in logged
    assert "Let me look" not in logged
    finished = next(r for r in caplog.records if r.getMessage() == "agent run finished")
    assert finished.live_deltas == 7  # type: ignore[attr-defined]
    assert finished.live_failures == 0  # type: ignore[attr-defined]


def test_built_partial_messages_follow_the_recorded_order(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    """The stream events the fake builds for a message have the shape and the order of the ones
    the pinned CLI printed for the same message (the recording)."""

    def shape(message: Any) -> tuple[str, ...]:
        if isinstance(message, StreamEvent):
            event = message.event
            inner = event.get("delta", {}).get("type") or event.get("content_block", {}).get("type")
            return ("stream_event", event["type"], str(inner or ""))
        if isinstance(message, AssistantMessage):
            return ("assistant", *(type(block).__name__ for block in message.content))
        return (type(message).__name__,)

    def collapse(shapes: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
        # How many deltas a block takes is not part of the order.
        out: list[tuple[str, ...]] = []
        for item in shapes:
            if out and out[-1] == item and item[1:2] == ("content_block_delta",):
                continue
            if item == ("SystemMessage",):
                continue  # status and thinking_tokens notices of the recording
            out.append(item)
        return out

    async def replay(name: str) -> list[tuple[str, ...]]:
        job = add_job(factory, alice_id, title=name)
        run_id = new_run(factory, alice_id, {"message": "x", "script": name}, job_id=job)
        seen: list[tuple[str, ...]] = []
        handle = runner._handle

        async def spy(message: Any, state: Any, recorder: Any) -> None:
            seen.append(shape(message))
            await handle(message, state, recorder)

        runner._handle = spy  # type: ignore[assignment]
        try:
            await runner.execute(
                live_context(settings, factory, keyring, RecordingStore(), speed=0.0), run_id
            )
        finally:
            runner._handle = handle  # type: ignore[assignment]
        return collapse(seen)

    recorded = asyncio.run(replay("partial_recorded"))
    built = asyncio.run(replay("job_chat"))  # thinking, text, tool call; then the final text
    assert recorded[:4] == [
        ("stream_event", "message_start", ""),
        ("stream_event", "content_block_start", "thinking"),
        ("stream_event", "content_block_delta", "thinking_delta"),
        ("stream_event", "content_block_delta", "signature_delta"),
    ]
    # In the recording each complete block arrives before its content_block_stop, and the tool
    # result before message_delta: the built stream must do the same.
    text_at = recorded.index(("assistant", "TextBlock"))
    assert recorded[text_at - 1] == ("stream_event", "content_block_delta", "text_delta")
    assert recorded[text_at + 1] == ("stream_event", "content_block_stop", "")
    tool_at = recorded.index(("assistant", "ToolUseBlock"))
    assert recorded[tool_at + 1 : tool_at + 5] == [
        ("stream_event", "content_block_stop", ""),
        ("UserMessage",),
        ("stream_event", "message_delta", ""),
        ("stream_event", "message_stop", ""),
    ]
    assert built == recorded


def test_thinking_text_is_never_live_stored_or_logged(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "hello"})  # echo: a thinking block with text
    with caplog.at_level(logging.DEBUG):
        assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    thought = "The user wants their message repeated."
    assert thought in (fake_backend.STREAMS / "echo.json").read_text(encoding="utf-8")
    assert store.written() == {1: "You said: hello"}
    assert thought not in store.everything()
    assert thought not in json.dumps(events(factory, run_id))
    logged = caplog.text + " ".join(str(record.__dict__) for record in caplog.records)
    assert thought not in logged
    assert "You said" not in logged  # no answer text in the logs either


def test_an_empty_answer_stores_and_publishes_no_text(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    name = script("empty", [assistant(text("")), result("")])
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    assert "text" not in kinds(factory, run_id)
    assert run_row(factory, run_id).output == {"text": ""}
    assert store.written() in ({}, {1: ""})
    assert store.meta == {}


def test_an_answer_in_several_messages_with_tool_calls_in_between(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    job = add_job(factory, alice_id)
    call = {"type": "tool_use", "id": "toolu_1", "name": "mcp__app__get_job", "input": {}}
    name = script(
        "several",
        [
            assistant(
                text("One. " + UNICODE), {**call, "input": {"job_id": str(job)}}, stop="tool_use"
            ),
            assistant(
                text("Two."),
                {**call, "id": "toolu_2", "input": {"job_id": str(job)}},
                stop="tool_use",
            ),
            assistant(text("Three, the answer: " + UNICODE)),
            result("Three, the answer: " + UNICODE),
        ],
        chars=3,
    )
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name}, job_id=job)
    assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    assert kinds(factory, run_id) == [
        "status",
        "status",
        "init",
        "text",
        "tool_call",
        "tool_result",
        "text",
        "tool_call",
        "tool_result",
        "text",
        "usage",
        "status",
    ]
    assert store.written() == {
        1: "One. " + UNICODE,
        2: "Two.",
        3: "Three, the answer: " + UNICODE,
    }
    # Each block is live after the steps stored before it: init, then each tool result.
    assert [op[1:] for op in store.ops if op[0] == "open"] == [(1, 3), (2, 6), (3, 9)]
    # Pieces of three characters cut accents, the combining mark and the emoji sequence apart.
    pieces = [op[2] for op in store.ops if op[0] == "append"]
    assert len(pieces) > 20
    assert run_row(factory, run_id).output == {"text": "Three, the answer: " + UNICODE}


def test_a_very_long_answer_is_stored_clipped_and_live_up_to_the_same_length(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    long = ("0123456789 " * 4000)[:40_000] + UNICODE
    name = script("long", [assistant(text(long)), result(long)], chars=997)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    rc = live_context(settings, factory, keyring, store)
    seen: list[runner._State] = []
    handle = runner._handle

    async def spy(message: Any, state: Any, recorder: Any) -> None:
        seen.append(state)
        await handle(message, state, recorder)

    runner._handle = spy  # type: ignore[assignment]
    try:
        assert execute(rc, run_id) is RunStatus.done
    finally:
        runner._handle = handle  # type: ignore[assignment]
    [stored_text] = [data["text"] for kind, data in events(factory, run_id) if kind == "text"]
    assert stored_text == long[: runner.TEXT_MAX] + f"... [{len(long) - runner.TEXT_MAX} more]"
    assert store.written() == {1: long[: runner.TEXT_MAX]}
    live_text = seen[0].live_text
    assert live_text is not None
    assert live_text.dropped == len(long) - runner.TEXT_MAX
    assert len(live_text._text) <= runner.TEXT_MAX  # the worker never held more than the cap


def test_the_credential_never_goes_live_even_when_the_answer_repeats_it(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    answer = f"You said: my key is {API_KEY}, twice {API_KEY}."
    name = script("leak", [assistant(text(answer)), result(answer)], chars=5)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    safe = answer.replace(API_KEY, runner.REDACTED)
    assert store.written() == {1: safe}
    assert API_KEY not in store.everything()
    assert API_KEY[:20] not in store.everything()
    assert run_row(factory, run_id).output == {"text": safe}


# --- every way a run ends -----------------------------------------------------------------------


def slow_answer(script: Any, name: str = "slowtext") -> tuple[str, str]:
    answer = "A long answer, written slowly. " * 40
    return script(name, [assistant(text(answer)), result(answer)], chars=10, sleep=0.05), answer


async def until_live(store: RecordingStore, size: int) -> None:
    while len(store.written().get(1, "")) < size:  # noqa: ASYNC110
        await asyncio.sleep(0.01)


def test_cancel_in_the_middle_of_an_answer(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    """Decision: a cancelled run stores no answer. The log keeps the cut block as a `text` step,
    because Claude Code reports it (recorded: an assistant message with the text so far, then
    the error result), exactly as without partial messages. Nothing stays live."""
    name, answer = slow_answer(script)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    rc = live_context(settings, factory, keyring, store, speed=1.0)

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        await until_live(store, 40)
        with factory() as db, db.begin():
            assert runs.request_cancel(db, alice_id, run_id, now()) is runs.CancelOutcome.requested
        return await task

    assert asyncio.run(go()) is RunStatus.cancelled
    row = run_row(factory, run_id)
    assert row.status is RunStatus.cancelled
    assert row.output is None
    evs = events(factory, run_id)
    assert evs[-1] == ("status", {"status": "cancelled"})
    [cut] = [data["text"] for kind, data in evs if kind == "text"]
    assert 40 <= len(cut) < len(answer)
    assert answer.startswith(cut)
    assert answer.startswith(store.written()[1])
    assert store.meta == {}
    assert store.text == {}


def test_a_cancelled_chat_answer_is_stored_nowhere(
    script: Any, migrated_url: str, key: str, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    user = add_user(factory, "ada@example.org")
    save_credential(factory, keyring, user)
    job = add_job(factory, user)
    name, _ = slow_answer(script)
    with factory() as db, db.begin():
        found = jobs_service.get_job(db, user, job)
        assert found is not None
        thread, _ = chat.thread_for_job(db, user, found, now())
        _, run = chat.send(db, user, thread, "hello", now())
        run.input = {**run.input, "script": name}
        run_id, thread_id = run.id, thread.id
    store = RecordingStore()
    rc = live_context(settings, factory, keyring, store, speed=1.0)

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        await until_live(store, 40)
        with factory() as db, db.begin():
            runs.request_cancel(db, user, run_id, now())
        return await task

    assert asyncio.run(go()) is RunStatus.cancelled
    with factory() as db:
        roles = db.scalars(
            sa.select(AgentMessage.role).where(AgentMessage.thread_id == thread_id)
        ).all()
        assert [role.value for role in roles] == ["user"]
        assert chat.active_run(db, thread_id) is None
    assert store.meta == {}


def test_a_run_failing_in_the_middle_of_an_answer(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    """The Claude Code process dies while a block is being written: no `text` step for it, the
    run fails, and the text that was live is gone."""
    partial = [
        {"type": "message_start", "message": {"id": "m", "content": []}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        *(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": f"piece {n} "},
            }
            for n in range(5)
        ),
    ]
    steps: list[dict[str, Any]] = [
        {
            "message": {
                "type": "stream_event",
                "uuid": str(uuid.uuid4()),
                "session_id": "{session_id}",
                "parent_tool_use_id": None,
                "event": event,
            }
        }
        for event in partial
    ]
    name = script("dies", [*steps, {"sleep": 5}, {"raise": "process"}])
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == "The Claude Code process failed (exit code 1)."
    assert row.output is None
    assert "text" not in kinds(factory, run_id)
    assert store.written() == {1: "piece 0 piece 1 piece 2 piece 3 piece 4 "}  # it was live
    assert store.ops[-1] == ("clear", 1)
    assert store.meta == {}
    assert store.text == {}


def test_worker_shutdown_in_the_middle_of_an_answer_clears_the_live_text(
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    name, _ = slow_answer(script)
    store = RecordingStore()
    run_id = new_run(factory, alice_id, {"message": "x", "script": name})
    rc = live_context(settings, factory, keyring, store, speed=1.0)

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        await until_live(store, 40)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    assert row.status is RunStatus.failed
    assert row.error == runs.STOPPED
    assert store.meta == {}
    assert store.text == {}


def test_a_dead_store_does_not_change_the_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redis down: the answer is not live, and the run and what it stores are as ever."""
    store = RecordingStore()
    store.fail = ConnectionError("redis is down")
    job = add_job(factory, alice_id)
    data = {"message": "hello", "script": "tool"}
    run_id = new_run(factory, alice_id, data, job_id=job)
    with caplog.at_level(logging.WARNING):
        assert execute(live_context(settings, factory, keyring, store), run_id) is RunStatus.done
    plain_run = new_run(factory, alice_id, data, job_id=job)
    assert (
        execute(context(settings, factory, keyring, Backends(speed=0.0)), plain_run)
        is RunStatus.done
    )
    assert stored(factory, run_id) == stored(factory, plain_run)
    assert store.ops == []
    assert caplog.text.count("live text not published") == 1  # once per run, not per delta


def test_a_broken_live_text_never_keeps_a_run_from_ending(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(self: Any) -> None:
        raise RuntimeError("bug in aclose")

    monkeypatch.setattr("app.agents.live.LiveText.aclose", explode)
    run_id = new_run(factory, alice_id, {"message": "hello"})
    rc = live_context(settings, factory, keyring, RecordingStore())
    assert execute(rc, run_id) is RunStatus.done
    assert run_row(factory, run_id).status is RunStatus.done


def test_live_text_is_off_without_a_store_and_with_the_setting_off(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    from app import worker

    ctx: dict[str, Any] = {"settings": settings, "sessions": factory, "clock": now}
    assert worker.agent_context(ctx).live_store is None  # no Redis pool in the context
    ctx["redis"] = object()
    assert worker.agent_context(ctx).live_store is not None
    store = RecordingStore()
    ctx["agent_live_store"] = store
    assert worker.agent_context(ctx).live_store is store
    off = settings.model_copy(update={"agents_live_text": False})
    assert worker.agent_context({**ctx, "settings": off}).live_store is None
