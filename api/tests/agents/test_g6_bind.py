"""A run bound in code to what it works on (ingestion G6, point 2): the definition's `bind` hook
answers when the run starts, the tools read the answer from their scope, and no tool takes an
id, so the model cannot point the run elsewhere. A run whose thing is gone fails before any
model call."""

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runner
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    MARKER,
    WRITTEN,
    Streams,
    agent,
    assistant,
    call,
    g6_tools,
    init,
    run_context,
    streams,
    text,
)
from tests.agents.run_helpers import (
    Backends,
    add_user,
    events,
    execute,
    new_run,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

__all__ = ["g6_tools", "streams"]


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


READ_THEN_WRITE = [
    init(),
    assistant("m1", call("t1", "g6_read")),
    assistant("m2", call("t2", "g6_write", text="what I found")),
    assistant("m3", text("Done.")),
]


def test_the_tools_read_what_the_hook_answered_and_take_no_id(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    seen: list[tuple[Any, Any, Any]] = []

    def bind(db: Session, run: AgentRun) -> dict[str, Any]:
        # A session of its own, and the run as claimed: its id, its user, its input.
        assert db.execute(sa.text("SELECT 1")).scalar_one() == 1
        seen.append((run.id, run.user_id, dict(run.input)))
        return {"document_id": "doc-7", "part": 2, "text": "the bound part"}

    bound = agent("g6_bound", bind=bind)
    streams.add("g6_bound", READ_THEN_WRITE)
    run_id = new_run(factory, alice_id, {"step_id": "s-1"}, agent="g6_bound")
    assert execute(run_context(settings, factory, keyring, bound), run_id) is RunStatus.done
    assert seen == [(run_id, alice_id, {"step_id": "s-1"})]  # asked once
    result = next(data for kind, data in events(factory, run_id) if kind == "tool_result")
    assert result["content"] == '{"untrusted_text": "the bound part"}'
    (user, binding, args) = WRITTEN[0]
    assert user == alice_id
    assert dict(binding) == {"document_id": "doc-7", "part": 2, "text": "the bound part"}
    assert args == {"text": "what I found"}  # the model gave a text, never an id


def test_an_id_the_model_adds_to_a_call_points_the_run_nowhere_else(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The model names another document in its arguments, as an injected text would ask it to.
    The tools have no such parameter: they read the binding, set in code."""
    bound = agent("g6_bound", bind=lambda db, run: {"document_id": "mine", "text": "my part"})
    streams.add(
        "g6_bound",
        [
            init(),
            assistant("m1", call("t1", "g6_read", document_id="someone-elses", offset=0)),
            assistant("m2", call("t2", "g6_write", text="x", document_id="someone-elses")),
            assistant("m3", text("Done.")),
        ],
    )
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    assert execute(run_context(settings, factory, keyring, bound), run_id) is RunStatus.done
    result = next(data for kind, data in events(factory, run_id) if kind == "tool_result")
    assert result["content"] == '{"untrusted_text": "my part"}'
    assert dict(WRITTEN[0][1])["document_id"] == "mine"


def test_each_run_is_bound_to_its_own_thing(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    bob = add_user(factory, "bob@example.org")
    save_credential(factory, keyring, bob)
    parts = {"a": "part of Alice", "b": "part of Bob"}
    bound = agent("g6_bound", bind=lambda db, run: {"text": parts[run.input["step"]]})
    streams.add("g6_bound", READ_THEN_WRITE)
    rc = run_context(settings, factory, keyring, bound)
    first = new_run(factory, alice_id, {"step": "a"}, agent="g6_bound")
    second = new_run(factory, bob, {"step": "b"}, agent="g6_bound")
    assert execute(rc, first) is RunStatus.done
    assert execute(rc, second) is RunStatus.done
    read = {
        run_id: next(d["content"] for k, d in events(factory, run_id) if k == "tool_result")
        for run_id in (first, second)
    }
    assert "part of Alice" in read[first]
    assert "part of Bob" in read[second]
    assert "Bob" not in read[first]
    assert [(user, dict(binding)["text"]) for user, binding, _ in WRITTEN] == [
        (alice_id, "part of Alice"),
        (bob, "part of Bob"),
    ]


def test_an_agent_without_the_hook_has_no_binding(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    streams.add("g6_free", READ_THEN_WRITE)
    run_id = new_run(factory, alice_id, {}, agent="g6_free")
    assert execute(run_context(settings, factory, keyring, agent("g6_free")), run_id) is (
        RunStatus.done
    )
    result = next(data for kind, data in events(factory, run_id) if kind == "tool_result")
    assert result == {"id": "t1", "is_error": True, "content": "Nothing is bound to this run."}
    assert WRITTEN[0][1] is None


def test_the_thing_is_gone_the_run_fails_before_any_model_call(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    asked: list[Any] = []

    def bind(db: Session, run: AgentRun) -> None:
        # Nothing has been read or started when the hook is asked.
        asked.append(db.get(AgentRun, run.id).credential_kind)  # type: ignore[union-attr]
        return None

    bound = agent("g6_bound", bind=bind)
    streams.add("g6_bound", READ_THEN_WRITE)
    backends = Backends()
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    rc = run_context(settings, factory, keyring, bound, backends=backends)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.BINDING_GONE == "This document no longer exists."
    assert row.model == "claude-opus-5-5"
    assert row.credential_kind is None  # the credential was never read
    assert (row.input_tokens, row.output_tokens, row.output) == (None, None, None)
    assert asked == [None]
    assert backends.made == []  # no model call, not even a backend
    assert WRITTEN == []
    assert events(factory, run_id) == [
        ("status", {"status": "queued"}),
        ("status", {"status": "running"}),
        ("status", {"status": "failed", "error": runner.BINDING_GONE}),
    ]
    assert list(Path(settings.agents_scratch_dir or "").iterdir()) == []
    assert runner.is_provider_failure(row.error) is False


@pytest.mark.parametrize("private", [False, True], ids=["public", "private"])
def test_a_hook_that_raises_fails_the_run_as_an_internal_error(
    private: bool,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def bind(db: Session, run: AgentRun) -> None:
        raise LookupError(f"could not read {MARKER}")

    bound = agent("g6_bound", bind=bind, private_io=private)
    streams.add("g6_bound", READ_THEN_WRITE)
    backends = Backends()
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    with caplog.at_level(logging.DEBUG):
        rc = run_context(settings, factory, keyring, bound, backends=backends)
        assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.INTERNAL
    assert backends.made == []
    crashed = [r for r in caplog.records if r.getMessage() == "agent run crashed"]
    assert len(crashed) == 1
    logged = caplog.text + capsys.readouterr().out
    if private:
        # Its type, never its words: a message can quote what the run read.
        assert crashed[0].error == "LookupError"  # type: ignore[attr-defined]
        assert crashed[0].exc_info is None
        assert MARKER not in logged
    else:
        assert crashed[0].exc_info is not None  # the traceback, as before this task
        # Its frames and type only: no message reaches a log line at all (issue #670).
        assert ", in bind\nLookupError" in logged
        assert MARKER not in logged


def test_what_the_hook_answers_cannot_be_changed_afterwards(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    answered = {"document_id": "doc-1", "positions": [1, 2]}
    rc = run_context(settings, factory, keyring)
    bound = runner._bind(rc, lambda db, run: answered, object())
    assert bound is not None
    with pytest.raises(TypeError):
        bound["document_id"] = "doc-2"  # type: ignore[index]
    with pytest.raises(TypeError):
        del bound["document_id"]  # type: ignore[attr-defined]
    answered["document_id"] = "changed by the hook's owner later"
    assert bound["document_id"] == "doc-1"  # a copy, taken when the run started
    assert runner._bind(rc, lambda db, run: None, object()) is None
    # An empty answer is an answer: only None says the thing is gone.
    empty = runner._bind(rc, lambda db, run: {}, object())
    assert empty is not None
    assert dict(empty) == {}


def test_the_binding_is_read_only_at_every_depth(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """Review of PR #95: a mapping that is read only one level deep leaves the list inside it
    open, and "no tool can change what another reads" would not hold for the positions of a
    transcription step. What the hook answers is frozen all the way down: the tools get tuples
    for lists and read only views for mappings."""
    doc = uuid.uuid4()
    answered: dict[str, Any] = {
        "document_id": doc,
        "positions": [3, 4, 5],
        "range": {"start": 0, "end": 100, "pages": [{"n": 1, "marks": {"a", "b"}}]},
        "part": 2,
        "note": None,
    }
    bound = agent("g6_bound", bind=lambda db, run: answered)
    streams.add("g6_bound", READ_THEN_WRITE)
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    assert execute(run_context(settings, factory, keyring, bound), run_id) is RunStatus.done
    seen = WRITTEN[0][1]  # the very object the tools were given
    assert seen["positions"] == (3, 4, 5)
    assert seen["document_id"] == doc
    assert (seen["part"], seen["note"]) == (2, None)
    assert seen["range"]["pages"][0]["marks"] == frozenset({"a", "b"})
    for change in (
        lambda: seen["positions"].append(6),
        lambda: seen["positions"].__setitem__(0, 9),
        lambda: seen["range"].__setitem__("end", 10**6),
        lambda: seen["range"]["pages"][0].__setitem__("n", 2),
        lambda: seen["range"]["pages"][0]["marks"].add("c"),
        lambda: seen.__setitem__("part", 3),
        lambda: seen.pop("part"),
    ):
        with pytest.raises((TypeError, AttributeError)):
            change()
    # Nor does the hook's own object reach in: the tools hold a copy at every depth.
    answered["positions"].append(99)
    answered["range"]["pages"][0]["n"] = 7
    assert seen["positions"] == (3, 4, 5)
    assert seen["range"]["pages"][0]["n"] == 1


def test_frozen() -> None:
    assert runner.frozen("text") == "text"
    assert runner.frozen(3) == 3
    assert runner.frozen(None) is None
    assert runner.frozen([1, [2, {"a": [3]}]])[1][1]["a"] == (3,)
    assert runner.frozen((1, [2])) == (1, (2,))
    assert runner.frozen({1, 2}) == frozenset({1, 2})
    assert type(runner.frozen({"a": 1})).__name__ == "mappingproxy"


def test_a_run_bound_to_an_empty_answer_still_runs(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    bound = agent("g6_bound", bind=lambda db, run: {})
    streams.add("g6_bound", READ_THEN_WRITE)
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    assert execute(run_context(settings, factory, keyring, bound), run_id) is RunStatus.done
    assert dict(WRITTEN[0][1]) == {}


def test_nothing_the_hook_writes_is_kept(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The hook reads. Its transaction ends without a commit."""

    def bind(db: Session, run: AgentRun) -> dict[str, Any]:
        db.execute(sa.update(AgentRun).where(AgentRun.id == run.id).values(error="by the hook"))
        return {"text": "x"}

    bound = agent("g6_bound", bind=bind)
    streams.add("g6_bound", READ_THEN_WRITE)
    run_id = new_run(factory, alice_id, {}, agent="g6_bound")
    assert execute(run_context(settings, factory, keyring, bound), run_id) is RunStatus.done
    assert run_row(factory, run_id).error is None
