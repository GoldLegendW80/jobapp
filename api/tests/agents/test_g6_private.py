"""A private run (ingestion G6, points 3 and 10) on the fake backend and a real Postgres: what
its tools are given and what they answer is never stored, only names and sizes; its output holds
counts; a tool name the model made up is not stored; nothing of it is live whatever the
settings; and a run that fails in the middle of a tool call leaves nothing of the document in
its events, its row or the log. MARKER stands for the document."""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ToolResultBlock, ToolUseBlock
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.conftest import API_KEY
from tests.agents.g6_helpers import (
    ALL_TOOLS,
    MARKER,
    SLOW_STARTED,
    WRITTEN,
    Streams,
    agent,
    assistant,
    call,
    everything_stored,
    g6_tools,
    init,
    raw_call,
    run_context,
    streams,
    text,
)
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    Backends,
    add_user,
    events,
    execute,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

__all__ = ["g6_tools", "streams"]

DOCUMENT = f"Camille Durand, engineer. {MARKER}. Started in 2021."
PROPOSED = f"Value copied from the document: {MARKER}"


def bound(db: Session, run: Any) -> dict[str, Any]:
    return {"document_id": "doc-1", "text": DOCUMENT}


PRIVATE = agent("g6_private", tools=ALL_TOOLS, private_io=True, bind=bound)
PUBLIC = agent("g6_private", tools=ALL_TOOLS, private_io=False, bind=bound)

EXTRACTION = [
    init(),
    assistant("m1", text("I will read the document."), call("t1", "g6_read")),
    assistant("m2", call("t2", "g6_write", text=PROPOSED)),
    assistant("m3", call("t3", "g6_image")),
    assistant("m4", text("Stored 1 item.")),
]


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


def logged(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    extras = " ".join(str(vars(record)) for record in caplog.records)
    return caplog.text + captured.out + captured.err + extras


def test_tool_events_hold_names_and_sizes_and_the_output_holds_counts(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    streams.add("g6_private", EXTRACTION)
    run_id = new_run(factory, alice_id, {"step_id": "s1"}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        assert execute(run_context(settings, factory, keyring, PRIVATE), run_id) is RunStatus.done
    evs = events(factory, run_id)
    calls = [data for kind, data in evs if kind == "tool_call"]
    results = [data for kind, data in evs if kind == "tool_result"]
    assert calls == [
        {"id": "t1", "name": "mcp__app__g6_read", "input_size": 2},
        {"id": "t2", "name": "mcp__app__g6_write", "input_size": len(PROPOSED) + 11},
        {"id": "t3", "name": "mcp__app__g6_image", "input_size": 2},
    ]
    read_size = len('{"untrusted_text": "' + DOCUMENT + '"}')
    assert results == [
        {"id": "t1", "is_error": False, "size": read_size, "blocks": 0},
        {"id": "t2", "is_error": False, "size": len("stored"), "blocks": 0},
        # An image block is counted, never described.
        {"id": "t3", "is_error": False, "size": len("page 1"), "blocks": 1},
    ]
    # The model's own words are stored as for any run (deleted with the document by its owner).
    assert [data["text"] for kind, data in evs if kind == "text"] == [
        "I will read the document.",
        "Stored 1 item.",
    ]
    row = run_row(factory, run_id)
    assert row.output == {
        "turns": 4,
        "text_blocks": 2,
        "text_chars": len("I will read the document.") + len("Stored 1 item."),
        "tool_calls": 3,
        "tool_errors": 0,
        "tools_denied": 0,
    }
    assert all(type(value) is int for value in row.output.values())  # counts only
    # The tools did get the values: the run worked on the document, nothing of it was kept.
    assert WRITTEN[0][2] == {"text": PROPOSED}
    assert MARKER not in everything_stored(factory, run_id)
    assert MARKER not in logged(caplog, capsys)
    assert "Camille" not in everything_stored(factory, run_id)


def test_the_same_run_of_a_public_agent_stores_values_as_before(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The control: the flag is the only difference between the two runs."""
    streams.add("g6_private", EXTRACTION)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    assert execute(run_context(settings, factory, keyring, PUBLIC), run_id) is RunStatus.done
    evs = events(factory, run_id)
    assert [data for kind, data in evs if kind == "tool_call"][1] == {
        "id": "t2",
        "name": "mcp__app__g6_write",
        "input": {"text": PROPOSED},
    }
    first = next(data for kind, data in evs if kind == "tool_result")
    assert MARKER in first["content"]
    assert "size" not in first
    assert run_row(factory, run_id).output == {"text": "Stored 1 item."}


def test_a_text_event_is_scrubbed_whole_then_shortened(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """What a private run does store goes through the rules of every stored text (issue #80):
    the credential replaced in the whole text first, the cut afterwards."""
    long = "x" * (runner.TEXT_MAX - 5) + API_KEY + " and more"
    streams.add(
        "g6_private",
        [init(), assistant("m1", text(long), call("t1", "g6_write", text=API_KEY))],
    )
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    assert execute(run_context(settings, factory, keyring, PRIVATE), run_id) is RunStatus.done
    stored = next(data["text"] for kind, data in events(factory, run_id) if kind == "text")
    assert stored.startswith("x" * (runner.TEXT_MAX - 5) + "[reda")
    assert API_KEY[:12] not in everything_stored(factory, run_id)
    assert run_row(factory, run_id).output["text_chars"] == len(long)  # type: ignore[index]


def test_a_tool_name_the_model_made_up_is_not_stored(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool's name is the model's text too. Only the names this code knows are kept: the
    agent's own tools, and Claude Code's built-in ones."""
    streams.add(
        "g6_private",
        [
            init(),
            assistant(
                "m1",
                raw_call("t1", f"mcp__app__{MARKER}", text="x"),
                raw_call("t2", "Bash", command="cat document"),
                raw_call("t3", f"store {MARKER} somewhere"),
                call("t4", "g6_write", text="fine"),
            ),
            assistant("m2", text("Done.")),
        ],
    )
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        assert execute(run_context(settings, factory, keyring, PRIVATE), run_id) is RunStatus.done
    evs = events(factory, run_id)
    names = [data["name"] for kind, data in evs if kind == "tool_call"]
    assert names == [runner.NOT_STORED, "Bash", runner.NOT_STORED, "mcp__app__g6_write"]
    denied = [data["name"] for kind, data in evs if kind == "tool_denied"]
    assert denied == [runner.NOT_STORED, "Bash", runner.NOT_STORED]
    warned = [r for r in caplog.records if r.getMessage() == "agent tool denied"]
    assert [r.tool for r in warned] == denied  # type: ignore[attr-defined]
    assert run_row(factory, run_id).output["tools_denied"] == 3  # type: ignore[index]
    assert MARKER not in everything_stored(factory, run_id)
    assert MARKER not in logged(caplog, capsys)


def test_a_tool_that_fails_with_the_document_in_its_message(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    streams.add(
        "g6_private",
        [init(), assistant("m1", call("t1", "g6_fail", text=MARKER)), assistant("m2", text("Ok."))],
    )
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        assert execute(run_context(settings, factory, keyring, PRIVATE), run_id) is RunStatus.done
    result = next(data for kind, data in events(factory, run_id) if kind == "tool_result")
    assert result == {
        "id": "t1",
        "is_error": True,
        "size": len(f"cannot store {MARKER}"),
        "blocks": 0,
    }
    assert run_row(factory, run_id).output["tool_errors"] == 1  # type: ignore[index]
    assert MARKER not in everything_stored(factory, run_id)
    assert MARKER not in logged(caplog, capsys)


# --- a run that fails in the middle of a tool call ----------------------------------------------

IN_FLIGHT = [init(), assistant("m1", text("Reading."), call("t1", "g6_slow", text=PROPOSED))]


def assert_nothing_of_the_document(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stored = everything_stored(factory, run_id)
    assert MARKER not in stored
    assert "Camille" not in stored
    assert MARKER not in logged(caplog, capsys)
    row = run_row(factory, run_id)
    assert row.output is not None
    assert all(type(value) is int for value in row.output.values())
    call_event = next(data for kind, data in events(factory, run_id) if kind == "tool_call")
    assert set(call_event) == {"id", "name", "input_size"}


def test_the_time_limit_passes_while_a_tool_call_is_in_flight(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dataclasses import replace

    streams.add("g6_private", IN_FLIGHT)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    rc = run_context(
        settings, factory, keyring, replace(PRIVATE, wall_time_s=1.5), interrupt_grace_s=0.2
    )
    with caplog.at_level(logging.DEBUG):
        assert execute(rc, run_id) is RunStatus.failed
    assert SLOW_STARTED.is_set()
    row = run_row(factory, run_id)
    assert row.error == "The run took longer than its limit of 1.5 seconds and was stopped."
    assert row.output == {
        "turns": 0,
        "text_blocks": 1,
        "text_chars": len("Reading."),
        "tool_calls": 1,
        "tool_errors": 0,
        "tools_denied": 0,
    }
    assert_nothing_of_the_document(factory, run_id, caplog, capsys)


def test_the_user_cancels_while_a_tool_call_is_in_flight(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    streams.add("g6_private", IN_FLIGHT)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    rc = run_context(settings, factory, keyring, PRIVATE, interrupt_grace_s=0.2)

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while not SLOW_STARTED.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        with factory() as db:
            runs.request_cancel(db, alice_id, run_id, now())
            db.commit()
        return await task

    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(go()) is RunStatus.cancelled
    assert run_row(factory, run_id).error is None
    assert_nothing_of_the_document(factory, run_id, caplog, capsys)


def test_the_worker_stops_while_a_tool_call_is_in_flight(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    streams.add("g6_private", IN_FLIGHT)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    rc = run_context(settings, factory, keyring, PRIVATE)

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while not SLOW_STARTED.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())
    assert run_row(factory, run_id).error == runs.STOPPED
    assert_nothing_of_the_document(factory, run_id, caplog, capsys)


def test_claude_code_dies_right_after_a_tool_call(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    streams.add(
        "g6_private",
        [
            init(),
            assistant("m1", call("t1", "g6_read")),
            assistant("m2", call("t2", "g6_write", text=PROPOSED)),
            {"raise": "process"},
        ],
    )
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        rc = run_context(settings, factory, keyring, PRIVATE)
        assert execute(rc, run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == "The Claude Code process failed (exit code 1)."
    assert run_row(factory, run_id).output["tool_calls"] == 2  # type: ignore[index]
    assert_nothing_of_the_document(factory, run_id, caplog, capsys)


def test_storing_the_result_of_a_tool_call_fails(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure sits between the call and its stored result, and its message quotes what it
    was handling, as a driver's error can. Only its type reaches the log."""
    streams.add("g6_private", EXTRACTION)
    original = runs.append_event

    def failing(db: Any, run_id: Any, seq: int, kind: str, data: Any, at: Any) -> None:
        if kind == "tool_result":
            raise RuntimeError(f"could not write the result of a call on {DOCUMENT}")
        original(db, run_id, seq, kind, data, at)

    monkeypatch.setattr(runs, "append_event", failing)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        rc = run_context(settings, factory, keyring, PRIVATE)
        assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.INTERNAL
    assert row.output is not None
    assert row.output["tool_calls"] == 1
    errors = [r for r in caplog.records if r.getMessage() == "agent run error"]
    assert [r.error for r in errors] == ["RuntimeError"]  # type: ignore[attr-defined]
    # (The event that could not be written leaves a hole in the run's seq, as for any run.)
    stored = everything_stored(factory, run_id)
    assert '"kind": "tool_call"' in stored
    assert '"kind": "tool_result"' not in stored
    assert MARKER not in stored
    assert MARKER not in logged(caplog, capsys)


def test_the_end_of_a_private_run_cannot_be_recorded(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streams.add("g6_private", EXTRACTION)

    def failing(*_: Any, **__: Any) -> bool:
        raise RuntimeError(f"could not finish the run that read {MARKER}")

    monkeypatch.setattr(runs, "finish", failing)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    with caplog.at_level(logging.DEBUG):
        rc = run_context(settings, factory, keyring, PRIVATE)
        assert execute(rc, run_id) is RunStatus.done  # what the run was; its row says running
    assert run_row(factory, run_id).status is RunStatus.running
    failed = [r for r in caplog.records if r.getMessage() == "agent run end not recorded"]
    assert [(r.error, r.exc_info) for r in failed] == [("RuntimeError", None)]  # type: ignore[attr-defined]
    assert MARKER not in logged(caplog, capsys)


@pytest.mark.parametrize("private", [True, False], ids=["private", "public, the control"])
@pytest.mark.parametrize("site", ["the stream", "the runner", "the end", "the hook"])
def test_a_log_handler_that_cannot_write_does_not_print_the_failure_either(
    site: str,
    private: bool,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a log handler fails, Python prints the failure of the handler together with the
    exception that was being handled at that moment, message included. Found by this suite: a
    handler left on a closed stream printed a test's document. A private run therefore logs
    its failures once they are no longer being handled, in each of the four places."""
    import io
    from dataclasses import replace

    def failing(*_: Any, **__: Any) -> Any:
        raise RuntimeError(f"it broke while handling {MARKER}")

    async def hook(final: definitions.FinalRun) -> None:
        failing()

    found = replace(PRIVATE, private_io=private)
    if site == "the stream":
        original = runs.append_event

        def on_result(db: Any, run_id: Any, seq: int, kind: str, data: Any, at: Any) -> None:
            if kind == "tool_result":
                failing()
            original(db, run_id, seq, kind, data, at)

        monkeypatch.setattr(runs, "append_event", on_result)
    elif site == "the runner":
        found = replace(found, bind=failing)
    elif site == "the end":
        monkeypatch.setattr(runs, "finish", failing)
    else:
        found = replace(found, on_final=hook)
    closed = io.StringIO()
    handler = logging.StreamHandler(closed)
    closed.close()
    logger = logging.getLogger("app.agents.runner")
    logger.addHandler(handler)
    streams.add("g6_private", EXTRACTION)
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    try:
        execute(run_context(settings, factory, keyring, found), run_id)
    finally:
        logger.removeHandler(handler)
    printed = capsys.readouterr().err
    assert "--- Logging error ---" in printed  # the handler did fail, and Python said so
    if private:
        assert MARKER not in printed
    else:
        assert MARKER in printed  # what the same failure prints for any other run


def test_a_private_run_that_never_reached_the_model_still_has_counts(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    streams: Streams,
    g6_tools: None,
) -> None:
    user = add_user(factory, "nokey@example.org")  # no credential
    run_id = new_run(factory, user, {}, agent="g6_private")
    assert execute(run_context(settings, factory, keyring, PRIVATE), run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.NO_CREDENTIAL
    assert row.output == dict.fromkeys(
        ("turns", "text_blocks", "text_chars", "tool_calls", "tool_errors", "tools_denied"), 0
    )


# --- nothing live -----------------------------------------------------------------------------

SPOKEN = f"The document says: {MARKER}. " * 20


@pytest.mark.parametrize("live_text", [True, False])
def test_a_private_run_publishes_nothing_whatever_the_setting(
    live_text: bool,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """AGENTS_LIVE_TEXT is on (the worker has a live store) and the definition does not even
    turn its own live text off: private_io alone keeps the publisher from being made."""
    from dataclasses import replace

    assert settings.agents_live_text is True
    private = replace(PRIVATE, live_text=live_text)
    assert private.live is False
    streams.add("g6_private", [init(), assistant("m1", text(SPOKEN))], partial={"chars": 7})
    store = RecordingStore()
    backends = Backends()
    run_id = new_run(factory, alice_id, {}, agent="g6_private")
    rc = run_context(settings, factory, keyring, private, backends=backends, live_store=store)
    assert execute(rc, run_id) is RunStatus.done
    assert store.ops == []
    assert store.writes == 0  # not even a clear
    assert backends.made[0].seen_options.include_partial_messages is False
    # The answer is in the log (the model's words), and only there: the output holds counts.
    assert [data["text"] for kind, data in events(factory, run_id) if kind == "text"] == [SPOKEN]
    assert MARKER not in str(run_row(factory, run_id).output)


def test_a_public_run_beside_it_still_streams(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    streams.add("g6_private", [init(), assistant("m1", text(SPOKEN))], partial={"chars": 7})
    store = RecordingStore()
    backends = Backends()
    rc = run_context(
        settings, factory, keyring, PUBLIC, backends=backends, live_store=store, live_flush_s=0.01
    )
    private_rc = run_context(settings, factory, keyring, PRIVATE, live_store=store)
    private_run = new_run(factory, alice_id, {}, agent="g6_private")
    assert execute(private_rc, private_run) is RunStatus.done
    assert store.ops == []
    public_run = new_run(factory, alice_id, {}, agent="g6_private")
    assert execute(rc, public_run) is RunStatus.done
    assert backends.made[0].seen_options.include_partial_messages is True
    assert SPOKEN.startswith(store.written()[1])
    assert store.written()[1]  # something of the public answer was live
    assert store.meta == {}  # and nothing is left


def test_the_run_stream_makes_no_feed_for_a_private_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    assert definitions.live_text("g6_private") is False  # an agent this process does not know
    for found in (PRIVATE, replace(PRIVATE, live_text=True), replace(PUBLIC, live_text=False)):
        monkeypatch.setitem(definitions.AGENTS, found.name, found)
        assert definitions.live_text(found.name) is False
    monkeypatch.setitem(definitions.AGENTS, PUBLIC.name, PUBLIC)
    assert definitions.live_text(PUBLIC.name) is True
    assert definitions.live_text("echo") is True


def test_a_chat_agent_cannot_be_private() -> None:
    with pytest.raises(ValueError, match="chat agent cannot be private_io"):
        agent("g6_private", private_io=True, chat=True)


# --- the pieces -----------------------------------------------------------------------------------


def test_sizes() -> None:
    assert runner.size_of({}) == 2
    assert runner.size_of({"text": "é日"}) == len('{"text":"é日"}')  # characters, not bytes
    assert runner.size_of(["a", 1]) == len('["a",1]')
    assert runner.size_of({"when": uuid.UUID(int=0)}) > 0  # anything is measured, never raised
    assert runner.size_of({1j: "a key JSON cannot write"}) == len("{1j: 'a key JSON cannot write'}")
    loop: list[Any] = []
    loop.append(loop)
    assert runner.size_of(loop) == len("[[...]]")
    assert runner.result_size(None) == (0, 0)
    assert runner.result_size("plain") == (5, 0)
    assert runner.result_size(
        [{"type": "text", "text": "ab"}, {"type": "image"}, "odd", {"type": "text"}]
    ) == (2, 2)
    assert runner.result_size(42) == (2, 0)


def test_the_recorder_keeps_only_the_names_it_knows() -> None:
    private = runner.Recorder(None, uuid.uuid4(), 1, private=True)  # type: ignore[arg-type]
    private.known_tools = frozenset({"mcp__app__g6_read"}) | frozenset(runner.FORBIDDEN_TOOLS)
    assert private.tool_name("mcp__app__g6_read") == "mcp__app__g6_read"
    assert private.tool_name("Read") == "Read"
    assert private.tool_name("mcp__app__g6_reader") == runner.NOT_STORED
    assert private.tool_name("") == runner.NOT_STORED
    public = runner.Recorder(None, uuid.uuid4(), 1)  # type: ignore[arg-type]
    assert public.tool_name("anything at all") == "anything at all"
    block = ToolUseBlock(id="t", name="made up", input={"a": "b"})
    assert private.tool_call(block) == {"id": "t", "name": runner.NOT_STORED, "input_size": 9}
    assert public.tool_call(block) == {"id": "t", "name": "made up", "input": {"a": "b"}}
    answer = ToolResultBlock(tool_use_id="t", content="secret text", is_error=None)
    assert private.tool_result(answer) == {"id": "t", "is_error": False, "size": 11, "blocks": 0}
    assert public.tool_result(answer) == {"id": "t", "is_error": False, "content": "secret text"}
