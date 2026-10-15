"""The run's credential never reaches anything stored, logged or published, wherever a text is
shortened (issue #80, point 1).

The bug: the stored `text` event was cut to TEXT_MAX characters first and scrubbed after, so a
credential lying across character 20,000 was cut in two, the scrub no longer recognised it, and
its beginning was stored as it was. The same order was used for tool results (TOOL_RESULT_MAX),
tool arguments (ARG_VALUE_MAX) and the run's final text. Every place is now scrubbed whole, then
shortened (`runner.shorten`).

Property style: for every place a text is cut, the credential is put at every offset around
the cut (whole before it, across it at every split, right after it), for both kinds of
credential and for the longest one the app accepts. First through the runner's own handlers with
a recorder that keeps what would be written (every offset), then through whole runs on a real
Postgres (the events, the run, the chat answer, a proposal, the logs and the live buffer).

Every credential here is made up (tests/agents/conftest.py): never a real one.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from sqlalchemy.orm import Session, sessionmaker

from app.agents import chat, definitions, live, proposals, runner, runs
from app.agents.crypto import Keyring
from app.agents.models import AgentMessage, CredentialKind, RunStatus
from app.config import Settings
from app.jobs import service as jobs_service
from tests.agents.conftest import API_KEY, OAUTH
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    events,
    execute,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.agents.test_live_runner import assistant, result, script, text
from tests.agents.test_live_text import delta, start
from tests.conftest import make_settings

__all__ = ["script"]

# The longest credential the app accepts (credentials.MAX_LENGTH), made up like the others.
LONGEST = "sk-ant-api03-" + "Zq8_" * 96 + "End"
SECRETS = {"api key": API_KEY, "subscription token": OAUTH, "longest credential": LONGEST}
KINDS = {
    "api key": (CredentialKind.anthropic_api_key, API_KEY),
    "subscription token": (CredentialKind.claude_subscription, OAUTH),
}
FILL = "."
TAIL = " and what follows"
# Accents, a combining mark, CJK and a joined emoji sequence: a cut every few code points falls
# inside a grapheme.
UNICODE = "é\U0001f469‍\U0001f4bb日é"
RUN = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
# The clock of the live writers here never moves (issue #293): real time decides nothing. With
# time.monotonic, a store call that a busy CPU delayed past STORE_SLOW_S started the back off
# and held the rest of the text back; the back off has its own tests (test_live_hardening.py).
STILL = 0.0

secrets = pytest.mark.parametrize("secret", list(SECRETS.values()), ids=list(SECRETS))


def fragment(stored: Any, secret: str, least: int = 8) -> str | None:
    """A piece of the secret, `least` characters or more, found in what was stored."""
    found = stored if isinstance(stored, str) else json.dumps(stored, ensure_ascii=False)
    for i in range(len(secret) - least + 1):
        if secret[i : i + least] in found:
            return secret[i : i + least]
    return None


def starts(limit: int, secret: str, lead: int = 0) -> range:
    """Where the secret begins, so that it lies whole before the cut, across it at every
    possible split, and right after it. `lead`: characters put before the text by an encoding."""
    return range(limit - len(secret) - 2 - lead, limit + 3)


def around(start_at: int, secret: str, fill: str = FILL) -> str:
    padding = (fill * (start_at // len(fill) + 1))[:start_at]
    return padding + secret + TAIL


def check(stored: str, raw: str, secret: str, limit: int, where: object) -> None:
    assert fragment(stored, secret) is None, f"a piece of the credential is stored ({where})"
    assert stored == runner._clip(raw.replace(secret, runner.REDACTED), limit), where
    stored.encode("utf-8")  # storable: no lone surrogate


@dataclass
class Captured(runner.Recorder):
    """The runner's recorder with its two writes kept in memory: everything before the write
    (the scrub, the order, the lock) is the code under test."""

    written: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    ended: list[runs.Outcome] = field(default_factory=list)

    def _write(self, seq: int, kind: str, data: dict[str, Any]) -> None:
        self.written.append((kind, data))

    def _finish(self, outcome: runs.Outcome) -> bool:
        self.ended.append(outcome)
        return True


def recorder(secret: str | None) -> Captured:
    return Captured(context=None, run_id=RUN, seq=1, secret=secret)  # type: ignore[arg-type]


def handled(message: Any, secret: str | None) -> list[tuple[str, dict[str, Any]]]:
    made = recorder(secret)
    asyncio.run(runner._handle(message, runner._State(), made))
    return made.written


def said(*blocks: Any) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="claude-test")


def ended(value: str | None) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        result=value,
    )


# --- every place a text is cut, every offset ----------------------------------------------------


@secrets
@pytest.mark.parametrize("fill", [FILL, UNICODE], ids=["ascii", "unicode"])
def test_the_text_event_at_every_offset_around_the_cut(secret: str, fill: str) -> None:
    for at in starts(runner.TEXT_MAX, secret):
        raw = around(at, secret, fill)
        [(kind, data)] = handled(said(TextBlock(raw)), secret)
        assert kind == "text"
        check(data["text"], raw, secret, runner.TEXT_MAX, at)


@secrets
@pytest.mark.parametrize("fill", [FILL, UNICODE], ids=["ascii", "unicode"])
def test_the_tool_result_at_every_offset_around_the_cut(secret: str, fill: str) -> None:
    for at in starts(runner.TOOL_RESULT_MAX, secret):
        raw = around(at, secret, fill)
        for content in (raw, [{"type": "text", "text": raw}]):
            message = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=content)])
            [(kind, data)] = handled(message, secret)
            assert kind == "tool_result"
            check(data["content"], raw, secret, runner.TOOL_RESULT_MAX, at)


@secrets
@pytest.mark.parametrize("fill", [FILL, UNICODE], ids=["ascii", "unicode"])
def test_a_tool_argument_at_every_offset_around_the_cut(secret: str, fill: str) -> None:
    """A text argument, an argument that is encoded as JSON before it is cut (its text then
    begins a few characters in), and arguments that are not an object at all."""
    limit = runner.ARG_VALUE_MAX
    for at in starts(limit, secret, lead=12):
        if at < 0:
            continue  # the longest credential does not fit before a cut at 200
        raw = around(at, secret, fill)
        nested, listed = {"q": [raw]}, [raw]
        [(_, data)] = handled(
            said(ToolUseBlock(id="t1", name="mcp__app__get_job", input={"a": raw, "b": nested})),
            secret,
        )
        check(data["input"]["a"], raw, secret, limit, at)
        check(data["input"]["b"], json.dumps(nested), secret, limit, at)
        [(_, data)] = handled(
            said(ToolUseBlock(id="t2", name="x", input=listed)),  # type: ignore[arg-type]
            secret,
        )
        check(data["input"]["value"], json.dumps(listed), secret, limit, at)


@secrets
def test_argument_names_tool_names_and_ids_are_scrubbed(secret: str) -> None:
    call = ToolUseBlock(
        id=f"id {secret}",
        name=f"name {secret}",
        input={secret: "one", f"x{secret}y": {secret: [secret]}},
    )
    written = handled(said(call), secret)
    assert fragment(written, secret) is None
    [(_, data)] = written
    assert data["id"] == data["name"].replace("name", "id") == f"id {runner.REDACTED}"
    assert set(data["input"]) == {runner.REDACTED, f"x{runner.REDACTED}y"}


@secrets
@pytest.mark.parametrize("fill", [FILL, UNICODE], ids=["ascii", "unicode"])
@pytest.mark.parametrize("source", ["result", "last text"])
def test_the_final_answer_at_every_offset_around_the_cut(
    secret: str, fill: str, source: str
) -> None:
    """The run's output (and with it the chat answer, which is this very text): the result
    message's text, or the last text block when the result holds none."""

    async def stored(raw: str) -> str:
        made, state = recorder(secret), runner._State()
        if source == "last text":
            await runner._handle(said(TextBlock(raw)), state, made)
        await runner._handle(ended(raw if source == "result" else None), state, made)
        assert await made.finish(runner.outcome_of(state, definitions.ECHO, None))
        [outcome] = made.ended
        assert outcome.status is RunStatus.done
        assert outcome.output is not None
        return str(outcome.output["text"])

    async def go() -> None:
        for at in starts(runner.TEXT_MAX, secret):
            raw = around(at, secret, fill)
            check(await stored(raw), raw, secret, runner.TEXT_MAX, at)

    asyncio.run(go())


@secrets
def test_the_error_and_the_model_of_a_run_are_scrubbed(secret: str) -> None:
    async def go() -> runs.Outcome:
        made = recorder(secret)
        outcome = runs.Outcome(
            status=RunStatus.failed, error=f"The agent failed ({secret}).", model=f"m {secret}"
        )
        assert await made.finish(outcome)
        return made.ended[0]

    clean = asyncio.run(go())
    assert clean.error == f"The agent failed ({runner.REDACTED})."
    assert clean.model == f"m {runner.REDACTED}"


@secrets
@pytest.mark.parametrize("size", [1, 7, 64])
@pytest.mark.parametrize("fill", [FILL, UNICODE], ids=["ascii", "unicode"])
def test_the_live_text_at_every_offset_around_its_cap(secret: str, size: int, fill: str) -> None:
    """The live buffer keeps the first `cap` characters of the block as the model wrote them and
    publishes them scrubbed, a flush after every delta (the worst case). A credential cut by the
    cap is a beginning of the credential at the end of the text: held back, never published.
    (This one already held before issue #80: kept as the guard of the same property.)"""
    cap = len(secret) + 150

    async def go() -> None:
        for at in starts(cap, secret):
            raw = around(at, secret, fill)
            store = RecordingStore()
            writer = live.LiveText(
                store, RUN, lambda: 3, secret=secret, cap=cap, flush_s=0, clock=lambda: STILL
            )
            writer.feed(start(0, text=""))
            for i in range(0, len(raw), size):
                writer.feed(delta(0, raw[i : i + size]))
                await writer._sync()
            written = store.written()[1]
            assert fragment(store.everything(), secret) is None, at
            assert raw.replace(secret, runner.REDACTED).startswith(written), at
            assert len(written) >= min(at, cap) - len(secret), at  # and it is not all held back

    asyncio.run(go())


@pytest.mark.parametrize("at", [19_900, 19_950, 19_999, 20_000])
def test_the_live_text_at_the_real_cap(at: int) -> None:
    raw = around(at, API_KEY)

    async def go() -> RecordingStore:
        store = RecordingStore()
        writer = live.LiveText(
            store,
            RUN,
            lambda: 3,
            secret=API_KEY,
            cap=runner.TEXT_MAX,
            flush_s=0,
            clock=lambda: STILL,
        )
        writer.feed(start(0, text=""))
        for i in range(0, len(raw), 997):
            writer.feed(delta(0, raw[i : i + 997]))
            await writer._sync()
        return store

    store = asyncio.run(go())
    assert fragment(store.everything(), API_KEY) is None
    assert raw.replace(API_KEY, runner.REDACTED).startswith(store.written()[1])


# --- what is not guaranteed: a credential in two values -----------------------------------------


def test_a_credential_cut_in_two_by_the_model_is_not_recognised() -> None:
    """The documented limit (`runner.scrub`): each stored value is scrubbed on its own. A
    credential whose beginning ends one text block and whose end begins the next is stored as
    the model wrote it, in two events; so are two arguments, and two content blocks of one tool
    result (joined with a line break). Wherever the pieces meet again in one string (the run's
    final text), the credential is whole and is replaced. This test holds the limit in place,
    so that changing it is a decision."""
    head, tail = API_KEY[:40], API_KEY[40:]
    written = handled(said(TextBlock(f"one {head}"), TextBlock(f"{tail} two")), API_KEY)
    assert [data["text"] for _, data in written] == [f"one {head}", f"{tail} two"]
    [(_, call)] = handled(
        said(ToolUseBlock(id="t", name="x", input={"a": head, "b": tail})), API_KEY
    )
    assert call["input"] == {"a": head, "b": tail}
    parts = [{"type": "text", "text": head}, {"type": "text", "text": tail}]
    [(_, answer)] = handled(
        UserMessage(content=[ToolResultBlock(tool_use_id="t", content=parts)]), API_KEY
    )
    assert answer["content"] == f"{head}\n{tail}"

    async def joined() -> runs.Outcome:
        made, state = recorder(API_KEY), runner._State()
        await runner._handle(ended(f"one {head}{tail} two"), state, made)
        await made.finish(runner.outcome_of(state, definitions.ECHO, None))
        return made.ended[0]

    assert asyncio.run(joined()).output == {"text": f"one {runner.REDACTED} two"}


def test_live_text_a_credential_across_two_deltas_is_caught_across_two_blocks_is_not() -> None:
    """Inside one block the live text is one growing string: the credential is caught however
    the deltas cut it. Across two blocks the same limit as the stored events holds, with one
    difference: the beginning that ends the first block was held back and is never published."""
    head, tail = API_KEY[:40], API_KEY[40:]

    async def go() -> RecordingStore:
        store = RecordingStore()
        writer = live.LiveText(
            store, RUN, lambda: 3, secret=API_KEY, flush_s=0, clock=lambda: STILL
        )
        writer.feed(start(0, text=""))
        for piece in ("one ", head, tail, " two"):
            writer.feed(delta(0, piece))
            await writer._sync()
        await writer.close_block()
        writer.feed(start(0, text=""))
        writer.feed(delta(0, f"one {head}"))
        await writer._sync()
        await writer.close_block()
        writer.feed(start(0, text=""))
        writer.feed(delta(0, f"{tail} two"))
        await writer._sync()
        return store

    written = asyncio.run(go()).written()
    assert written == {1: f"one {runner.REDACTED} two", 2: "one ", 3: f"{tail} two"}


def test_a_secret_that_json_would_escape_is_scrubbed_before_the_encoding() -> None:
    """A credential holds letters, digits, "-" and "_" only (credentials.py), so encoding an
    argument as JSON cannot change it. The scrub does not rely on that: it runs on the
    arguments as they are, before the encoding."""
    odd = 'pä"ss\\wörd-' + "Ab1_" * 8
    [(_, data)] = handled(said(ToolUseBlock(id="t", name="x", input={"nested": {"k": [odd]}})), odd)
    assert data["input"]["nested"] == json.dumps({"k": [runner.REDACTED]})


def test_without_a_secret_nothing_changes() -> None:
    raw = around(runner.TEXT_MAX - 10, API_KEY)
    [(_, data)] = handled(said(TextBlock(raw)), None)
    assert data["text"] == runner._clip(raw, runner.TEXT_MAX)
    assert runner.shorten("abc", 2, None) == "ab... [1 more]"
    assert runner.shorten("abc", 3, "b") == "a[redacted]c"[:3] + "... [9 more]"
    assert runner.summarize_args({"a": 1}) == {"a": "1"}


# --- whole runs on a real Postgres --------------------------------------------------------------


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(
        migrated_url, credentials_key=key, agents_scratch_dir=str(scratch), agents_fake_backend=True
    )


def call(call_id: str, name: str, given: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": given}


def user_with(
    factory: sessionmaker[Session], keyring: Keyring, kind: CredentialKind, secret: str
) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user, secret=secret, kind=kind)
    return user


# Where the secret begins, counted from the cut: whole before it, one character over it, in the
# middle, all but one character over it, and right at it.
SHIFTS = {"whole before": None, "one over": 1, "middle": 50, "one left": -1, "at the cut": 0}


def begin(limit: int, secret: str, shift: int | None) -> int:
    if shift is None:
        return limit - len(secret) - 1
    if shift < 0:
        return limit + shift
    return limit - len(secret) + shift if shift else limit


@pytest.mark.parametrize("name", list(KINDS))
@pytest.mark.parametrize("shift", list(SHIFTS.values()), ids=list(SHIFTS))
def test_a_run_stores_logs_and_publishes_no_piece_of_the_credential(
    name: str,
    shift: int | None,
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One run whose stream holds the credential across every cut at once: a text block
    (20,000), a tool argument (200), the result of a refused tool (2,000, the refusal quotes the
    tool's name), a second refused tool whose short name is logged, the model's name, and the
    final answer (20,000). Read back from Postgres, the logs and the live buffer."""
    kind, secret = KINDS[name]
    user = user_with(factory, keyring, kind, secret)
    job = add_job(factory, user)
    answer = around(begin(runner.TEXT_MAX, secret, shift), secret, UNICODE)
    final = around(begin(runner.TEXT_MAX, secret, shift), secret)
    argument = around(begin(runner.ARG_VALUE_MAX, secret, shift), secret)
    refusal = "The tool "  # runner.make_gate: the result of a refused tool begins with this
    long_name = around(begin(runner.TOOL_RESULT_MAX, secret, shift) - len(refusal), secret)
    said_by = assistant(
        text(answer),
        call("t1", "mcp__app__get_job", {"job_id": argument}),
        call("t2", long_name, {}),
        call("t3", f"leak_{secret}", {secret: secret}),
        stop="tool_use",
    )
    said_by["message"]["message"]["model"] = f"relay-{secret}"
    stream = script("straddle", [said_by, result(final)], chars=997)
    store = RecordingStore()
    run_id = new_run(factory, user, {"message": "x", "script": stream}, job_id=job)
    rc = replace(context(settings, factory, keyring, Backends(speed=0.0)), live_store=store)
    with caplog.at_level(logging.DEBUG):
        assert execute(replace(rc, live_flush_s=0.0), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    stored = events(factory, run_id)
    logged = caplog.text + "".join(str(vars(r)) for r in caplog.records) + capsys.readouterr().out
    for what, value in {
        "events": stored,
        "output": row.output,
        "error": row.error,
        "model": row.model,
        "logs": logged,
        "live": store.everything(),
    }.items():
        assert fragment(value, secret) is None, f"a piece of the credential in the {what}"
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for event_kind, data in stored:
        by_kind.setdefault(event_kind, []).append(data)
    assert by_kind["text"] == [
        {"text": runner._clip(answer.replace(secret, runner.REDACTED), runner.TEXT_MAX)}
    ]
    assert row.output == {
        "text": runner._clip(final.replace(secret, runner.REDACTED), runner.TEXT_MAX)
    }
    assert row.model == f"relay-{runner.REDACTED}"
    assert by_kind["tool_call"][0]["input"] == {
        "job_id": runner._clip(argument.replace(secret, runner.REDACTED), runner.ARG_VALUE_MAX)
    }
    assert by_kind["tool_call"][2]["input"] == {runner.REDACTED: runner.REDACTED}
    said_no = f"{refusal}{long_name} is not available to this agent."
    assert by_kind["tool_result"][1]["content"] == runner._clip(
        said_no.replace(secret, runner.REDACTED), runner.TOOL_RESULT_MAX
    )
    assert [data["name"] for data in by_kind["tool_denied"]] == [
        long_name.replace(secret, runner.REDACTED),
        f"leak_{runner.REDACTED}",
    ]
    denied = [r for r in caplog.records if r.getMessage() == "agent tool denied"]
    assert [r.tool for r in denied][1] == f"leak_{runner.REDACTED}"
    assert len(denied[0].tool) < runner.ARG_VALUE_MAX + 40  # the long name is shortened there


@pytest.mark.parametrize("name", list(KINDS))
@pytest.mark.parametrize("shift", list(SHIFTS.values()), ids=list(SHIFTS))
def test_the_chat_answer_holds_no_piece_of_the_credential(
    name: str,
    shift: int | None,
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
) -> None:
    kind, secret = KINDS[name]
    user = user_with(factory, keyring, kind, secret)
    job = add_job(factory, user)
    final = around(begin(runner.TEXT_MAX, secret, shift), secret, UNICODE)
    stream = script("chatstraddle", [assistant(text(final)), result(final)])
    with factory() as db, db.begin():
        found = jobs_service.get_job(db, user, job)
        assert found is not None
        thread, _ = chat.thread_for_job(db, user, found, now())
        _, run = chat.send(db, user, thread, "What does the posting ask for?", now())
        run.input = {**run.input, "script": stream}
        run_id, thread_id = run.id, thread.id
    assert execute(context(settings, factory, keyring, Backends(speed=0.0)), run_id) is (
        RunStatus.done
    )
    with factory() as db:
        rows = db.scalars(
            sa.select(AgentMessage)
            .where(AgentMessage.thread_id == thread_id)
            .order_by(AgentMessage.id)
        ).all()
        answers = [row.content for row in rows if row.role.value == "assistant"]
    expected = runner._clip(final.replace(secret, runner.REDACTED), runner.TEXT_MAX)
    assert answers == [{"text": expected}]
    assert fragment(answers, secret) is None
    assert run_row(factory, run_id).output == {"text": expected}


@pytest.mark.parametrize("name", list(KINDS))
def test_a_proposal_holds_no_credential(
    name: str,
    script: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
) -> None:
    """What the model writes into propose_note and propose_status is stored in `proposals`:
    scrubbed like an event, before the length checks and the whitespace clean up."""
    kind, secret = KINDS[name]
    user = user_with(factory, keyring, kind, secret)
    job = add_job(factory, user)
    note = f"  Possible fit.\r\nYour key is {secret}, twice {secret}.  "
    reason = f"Weak   fit, and {secret}\n was in the posting."
    calls = assistant(
        call("t1", "mcp__app__propose_note", {"text": note}),
        call("t2", "mcp__app__propose_status", {"status": "skipped", "reason": reason}),
        stop="tool_use",
    )
    stream = script("proposing", [calls, result("Done.")])
    run_id = new_run(factory, user, {"script": stream}, job_id=job, agent="triage")
    assert execute(context(settings, factory, keyring, Backends(speed=0.0)), run_id) is (
        RunStatus.done
    )
    with factory() as db:
        made = list(proposals.list_proposals(db, user, proposals.Filters(), 50))
        payloads = sorted((row.kind, row.payload) for row in made)
    assert fragment(payloads, secret) is None
    assert payloads == [
        (
            "job_note",
            {"text": f"Possible fit.\nYour key is {runner.REDACTED}, twice {runner.REDACTED}."},
        ),
        (
            "job_status",
            {
                "status": "skipped",
                "from": "found",
                "reason": f"Weak fit, and {runner.REDACTED} was in the posting.",
            },
        ),
    ]
    assert fragment(events(factory, run_id), secret) is None


def test_a_note_too_long_only_because_of_the_credential_is_judged_after_the_scrub(
    script: Any, settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    """Scrub first, measure after: a note of NOTE_MAX characters once the credential is
    replaced is accepted, although what the model wrote was longer."""
    user = user_with(factory, keyring, CredentialKind.anthropic_api_key, API_KEY)
    job = add_job(factory, user)
    note = API_KEY + "n" * (proposals.NOTE_MAX - len(runner.REDACTED))
    assert len(note) > proposals.NOTE_MAX
    proposing = assistant(call("t1", "mcp__app__propose_note", {"text": note}), stop="tool_use")
    stream = script("longnote", [proposing, result("Done.")])
    run_id = new_run(factory, user, {"script": stream}, job_id=job, agent="triage")
    assert execute(context(settings, factory, keyring, Backends(speed=0.0)), run_id) is (
        RunStatus.done
    )
    with factory() as db:
        [made] = list(proposals.list_proposals(db, user, proposals.Filters(), 50))
    assert made.payload == {"text": runner.REDACTED + "n" * (proposals.NOTE_MAX - 10)}
