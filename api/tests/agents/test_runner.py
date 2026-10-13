"""The runner (app/agents/runner.py) on the fake backend and a real Postgres: lifecycle, events,
limits, cancel, failures, scratch directory, credential handling, tool scoping and the gate."""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runner, runs
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, RunStatus
from app.config import Settings
from tests.agents.conftest import API_KEY, OAUTH
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    credential_row,
    events,
    execute,
    kinds,
    new_run,
    run_row,
    save_credential,
)
from tests.conftest import make_settings


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def scratch_root(settings: Settings) -> Path:
    assert settings.agents_scratch_dir is not None
    return Path(settings.agents_scratch_dir)


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


def test_echo_run_done_with_events_tokens_and_cost(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
) -> None:
    backends = Backends()
    run_id = new_run(factory, alice_id, {"message": "Bonjour, ça va ?"})
    status = execute(context(settings, factory, keyring, backends), run_id)
    assert status is RunStatus.done
    row = run_row(factory, run_id)
    assert row.status is RunStatus.done
    assert row.output == {"text": "You said: Bonjour, ça va ?"}
    assert row.error is None
    assert row.model == "claude-opus-5-5"
    # input + cache creation + cache read of the recorded result
    assert (row.input_tokens, row.output_tokens) == (920 + 1200 + 3400, 42)
    assert float(row.cost_usd or 0) == pytest.approx(0.0123)
    assert row.started_at is not None
    assert row.finished_at is not None
    assert kinds(factory, run_id) == [
        "status",
        "status",
        "init",
        "thinking",
        "text",
        "usage",
        "status",
    ]
    evs = events(factory, run_id)
    assert evs[0][1] == {"status": "queued"}
    assert evs[1][1] == {"status": "running"}
    assert evs[-1][1] == {"status": "done"}
    assert evs[3][1] == {}  # thinking content is never stored
    assert evs[5][1]["cost_usd"] == pytest.approx(0.0123)
    # The scratch directory is gone, and the backend ran inside it.
    assert list(scratch_root.iterdir()) == []
    cwd = backends.made[0].seen_cwd
    assert cwd is not None
    assert cwd.startswith(str(scratch_root))
    assert cwd.endswith("/work")


def test_env_carries_the_api_key_and_blanks_the_worker_secrets(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secretpw@db/x")
    monkeypatch.setenv("CREDENTIALS_KEY", "worker-secret-key")
    monkeypatch.setenv("SECRET_KEY", "session-signing-secret")
    backends = Backends()
    execute(context(settings, factory, keyring, backends), new_run(factory, alice_id))
    env = backends.made[0].seen_env
    assert env is not None
    assert env["ANTHROPIC_API_KEY"] == API_KEY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["DATABASE_URL"] == env["CREDENTIALS_KEY"] == env["SECRET_KEY"] == ""
    assert "PATH" not in env  # kept as inherited, never blanked
    assert os.environ["PATH"]
    for name in (
        "CLAUDE_CODE_SKIP_PROMPT_HISTORY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER",
        "DISABLE_TELEMETRY",
    ):
        assert env[name] == "1"
    assert env["CLAUDE_CONFIG_DIR"].endswith("/config")
    assert env["HOME"].endswith("/home")


def test_subscription_token_goes_to_oauth_variable_and_is_verified_once(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "sub@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    assert credential_row(factory, user).verified_at is None  # type: ignore[union-attr]
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), new_run(factory, user)) is (
        RunStatus.done
    )
    env = backends.made[0].seen_env or {}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAUTH
    assert env.get("ANTHROPIC_API_KEY", "") == ""
    assert credential_row(factory, user).verified_at is not None  # type: ignore[union-attr]


def test_failed_subscription_run_does_not_verify(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "sub@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    run_id = new_run(factory, user, {"message": "x", "script": "refused"})
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.REFUSED
    assert credential_row(factory, user).verified_at is None  # type: ignore[union-attr]


def test_token_replaced_during_the_run_is_not_marked_verified(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "sub@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    run_id = new_run(factory, user)
    replaced = OAUTH[:-4] + "New9"

    class Replacing(Backends):
        def __call__(self) -> Any:
            # A new token saved while the run is under way.
            save_credential(factory, keyring, user, replaced, CredentialKind.claude_subscription)
            return super().__call__()

    assert execute(context(settings, factory, keyring, Replacing()), run_id) is RunStatus.done
    row = credential_row(factory, user)
    assert row is not None
    assert row.verified_at is None
    assert row.last4 == "New9"


def test_tool_reads_the_users_own_job(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    job = add_job(factory, alice_id, "Mistral AI", "Applied AI Engineer")
    run_id = new_run(factory, alice_id, {"message": "read it", "script": "tool"}, job_id=job)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    evs = events(factory, run_id)
    call = next(data for kind, data in evs if kind == "tool_call")
    assert call == {
        "id": "toolu_fake_01",
        "name": "mcp__app__get_job",
        "input": {"job_id": str(job)},
    }
    result = next(data for kind, data in evs if kind == "tool_result")
    assert result["is_error"] is False
    assert "Mistral AI" in result["content"]
    assert "Applied AI Engineer" in result["content"]
    assert str(alice_id) not in result["content"]  # the user id never reaches the model


def test_tool_cannot_read_another_users_job_even_with_its_id(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    bob = add_user(factory, "bob@example.org")
    bobs_job = add_job(factory, bob, "Secret Corp", "Hidden Role")
    # Alice's run is told Bob's job id (the model choosing any id it likes).
    run_id = new_run(factory, alice_id, {"message": "x", "script": "tool"})
    rc = context(settings, factory, keyring)

    async def go() -> Any:
        from app.agents import fake_backend

        original = fake_backend.FakeBackend.stream

        async def with_bobs_id(self: Any, request: Any) -> Any:
            request.variables["job_id"] = str(bobs_job)
            async for message in original(self, request):
                yield message

        fake_backend.FakeBackend.stream = with_bobs_id  # type: ignore[method-assign]
        try:
            return await runner.execute(rc, run_id)
        finally:
            fake_backend.FakeBackend.stream = original  # type: ignore[method-assign]

    assert asyncio.run(go()) is RunStatus.done
    result = next(data for kind, data in events(factory, run_id) if kind == "tool_result")
    assert result == {"id": "toolu_fake_01", "is_error": True, "content": "Job not found."}
    for _, data in events(factory, run_id):
        assert "Secret Corp" not in str(data)
        assert "Hidden Role" not in str(data)


def test_forbidden_tools_are_refused_by_the_gate(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = add_job(factory, alice_id)
    run_id = new_run(factory, alice_id, {"message": "x", "script": "forbidden"}, job_id=job)
    with caplog.at_level(logging.INFO):
        assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    evs = events(factory, run_id)
    denied = [data["name"] for kind, data in evs if kind == "tool_denied"]
    assert denied == ["Bash", "Write", "WebFetch", "Read", "mcp__other__steal"]
    results = {data["id"]: data for kind, data in evs if kind == "tool_result"}
    for call in ("toolu_fake_b1", "toolu_fake_w1", "toolu_fake_f1", "toolu_fake_r1"):
        assert results[call]["is_error"] is True
        assert "is not available to this agent" in results[call]["content"]
    assert results["toolu_fake_g1"]["is_error"] is False
    warned = [r for r in caplog.records if r.getMessage() == "agent tool denied"]
    assert [r.tool for r in warned] == denied  # type: ignore[attr-defined]


def test_gate_allows_only_the_allow_list() -> None:
    seen: list[str] = []

    async def on_denied(name: str) -> None:
        seen.append(name)

    gate = runner.make_gate(frozenset({"mcp__app__get_job"}), on_denied)

    async def ask(name: str) -> dict[str, Any]:
        return await gate({"tool_name": name, "tool_input": {}}, "id", None)

    assert asyncio.run(ask("mcp__app__get_job")) == {}
    for name in ("Bash", "mcp__app__get_job2", "mcp__app__", "", "MCP__APP__GET_JOB"):
        out = asyncio.run(ask(name))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert seen == ["Bash", "mcp__app__get_job2", "mcp__app__", "", "MCP__APP__GET_JOB"]


def test_options_lock_the_agent_down(tmp_path: Path) -> None:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from app.agents import definitions
    from app.agents import tools as app_tools

    tools = app_tools.build(None, uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]

    async def gate(*_: Any) -> dict[str, Any]:
        return {}

    options = runner.build_options(
        definitions.ECHO, tools, {"ANTHROPIC_API_KEY": API_KEY}, tmp_path, gate
    )
    assert options.tools == []
    assert options.allowed_tools == ["mcp__app__get_job"]
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.model == "claude-opus-5-5"
    assert options.max_turns == definitions.ECHO.max_turns
    for name in ("Bash", "Write", "Edit", "Read", "WebFetch", "WebSearch", "Agent", "Task"):
        assert name in options.disallowed_tools
    assert options.resume is None
    assert options.continue_conversation is False
    # The command line the SDK would give Claude Code (built, never spawned).
    transport = SubprocessCLITransport(prompt="x", options=options)
    transport._cli_path = "/usr/bin/claude"
    cmd = transport._build_command()
    joined = " ".join(cmd)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__app__get_job"
    assert "Bash" in cmd[cmd.index("--disallowedTools") + 1].split(",")
    assert "--strict-mcp-config" in cmd
    assert "--setting-sources=" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert API_KEY not in joined  # the credential travels in the environment only
    assert "--resume" not in joined
    assert "--continue" not in cmd


def test_turn_limit_fails_the_run(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    job = add_job(factory, alice_id)
    run_id = new_run(factory, alice_id, {"message": "x", "script": "loop"}, job_id=job)
    rc = context(settings, factory, keyring, max_turns=3)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == "The run reached its limit of 3 turns."
    assert len([k for k in kinds(factory, run_id) if k == "tool_call"]) == 4


def test_wall_time_limit_stops_the_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
) -> None:
    run_id = new_run(factory, alice_id, {"message": "x", "script": "stall"})
    rc = context(settings, factory, keyring, Backends(speed=1.0), wall_time_s=0.5)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == "The run took longer than its limit of 0.5 seconds and was stopped."
    assert list(scratch_root.iterdir()) == []


def test_cancel_mid_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
) -> None:
    run_id = new_run(factory, alice_id, {"message": "x", "script": "slow"})
    rc = context(settings, factory, keyring, Backends(speed=0.1))

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        # Wait for a few steps, then cancel as the api does.
        while kinds(factory, run_id).count("text") < 3:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        with factory() as db:
            assert runs.request_cancel(db, alice_id, run_id, runs_now()) is (
                runs.CancelOutcome.requested
            )
            db.commit()
        return await task

    assert asyncio.run(go()) is RunStatus.cancelled
    row = run_row(factory, run_id)
    assert row.status is RunStatus.cancelled
    assert row.error is None
    texts = kinds(factory, run_id).count("text")
    assert 3 <= texts < 30
    assert events(factory, run_id)[-1] == ("status", {"status": "cancelled"})
    assert list(scratch_root.iterdir()) == []


def runs_now() -> Any:
    from tests.agents.run_helpers import now

    return now()


class Stubborn:
    """A backend that ignores interrupt(): the runner must cancel it after the grace period."""

    def __init__(self) -> None:
        self.interrupted = False

    async def interrupt(self) -> None:
        self.interrupted = True

    async def stream(self, request: Any) -> Any:
        await asyncio.sleep(3600)
        yield None  # pragma: no cover


def test_backend_ignoring_interrupt_is_cancelled_after_grace(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
) -> None:
    stubborn = Stubborn()
    rc = context(settings, factory, keyring, lambda: stubborn, wall_time_s=0.2)
    rc.interrupt_grace_s = 0.2
    run_id = new_run(factory, alice_id)
    assert execute(rc, run_id) is RunStatus.failed
    assert stubborn.interrupted
    assert "took longer" in (run_row(factory, run_id).error or "")
    assert list(scratch_root.iterdir()) == []


@pytest.mark.parametrize(
    ("script", "error"),
    [
        ("crash", "The Claude Code process failed (exit code 1)."),
        ("missing_cli", runner.NO_CLI),
        ("refused", runner.REFUSED),
        ("no_credit", runner.NO_CREDIT),
    ],
)
def test_failures_are_recorded_and_scratch_removed(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
    script: str,
    error: str,
) -> None:
    run_id = new_run(factory, alice_id, {"message": "x", "script": script})
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == error
    assert events(factory, run_id)[-1] == ("status", {"status": "failed", "error": error})
    assert list(scratch_root.iterdir()) == []


def test_no_credential_fails_without_a_scratch_dir(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, scratch_root: Path
) -> None:
    user = add_user(factory, "nokey@example.org")
    run_id = new_run(factory, user)
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.NO_CREDENTIAL
    assert backends.made == []
    assert list(scratch_root.iterdir()) == []


def test_unreadable_credential_and_missing_keyring(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    other = Keyring.from_text("A" * 43 + "=")
    run_id = new_run(factory, alice_id)
    assert execute(context(settings, factory, other), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.UNREADABLE
    run_id = new_run(factory, alice_id)
    assert execute(context(settings, factory, None), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.UNREADABLE


def test_unknown_agent_fails(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    run_id = new_run(factory, alice_id, agent="removed_agent")
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.UNKNOWN_AGENT


def test_a_run_not_queued_is_skipped(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, alice_id: uuid.UUID
) -> None:
    run_id = new_run(factory, alice_id)
    with factory() as db:
        assert runs.request_cancel(db, alice_id, run_id, runs_now()) is runs.CancelOutcome.cancelled
        db.commit()
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), run_id) is None
    assert backends.made == []
    assert run_row(factory, run_id).status is RunStatus.cancelled
    assert execute(context(settings, factory, keyring), uuid.uuid4()) is None


def test_worker_shutdown_fails_the_run_and_removes_scratch(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
) -> None:
    run_id = new_run(factory, alice_id, {"message": "x", "script": "slow"})
    rc = context(settings, factory, keyring, Backends(speed=1.0))

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while "init" not in kinds(factory, run_id):  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    assert row.status is RunStatus.failed
    assert row.error == runs.STOPPED
    assert list(scratch_root.iterdir()) == []


def test_secret_never_reaches_events_output_or_logs(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The model repeats whatever is in the message: here the user's own key.
    run_id = new_run(factory, alice_id, {"message": f"my key is {API_KEY}"})
    with caplog.at_level(logging.DEBUG):
        assert execute(context(settings, factory, keyring), run_id) is RunStatus.done
    row = run_row(factory, run_id)
    assert API_KEY not in str(row.output)
    assert row.output == {"text": f"You said: my key is {runner.REDACTED}"}
    for _, data in events(factory, run_id):
        assert API_KEY not in str(data)
    logged = caplog.text + capsys.readouterr().out
    assert API_KEY not in logged
    assert "my key is" not in logged  # no prompt text in the logs
    finished = [r for r in caplog.records if r.getMessage() == "agent run finished"]
    assert len(finished) == 1
    assert finished[0].status == "done"


def test_crash_inside_the_runner_is_recorded(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    scratch_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(runner.app_tools, "build", broken)
    run_id = new_run(factory, alice_id)
    assert execute(context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.INTERNAL
    assert list(scratch_root.iterdir()) == []


# --- pure helpers ---------------------------------------------------------------------------


def test_summarize_args_hides_secrets_and_clips() -> None:
    out = runner.summarize_args(
        {"job_id": "abc", "api_key": "x", "Token": "y", "long": "z" * 500, "n": {"a": [1, 2]}}
    )
    assert out["job_id"] == "abc"
    assert out["api_key"] == out["Token"] == runner.REDACTED
    assert out["long"].startswith("z" * 200)
    assert out["long"].endswith("[300 more]")
    assert out["n"] == '{"a": [1, 2]}'
    many = runner.summarize_args({f"k{i}": "v" * 190 for i in range(20)})
    assert many["..."] == "more arguments not shown"
    assert runner.summarize_args(["a"]) == {"value": '["a"]'}


def test_scrub_and_result_text() -> None:
    assert runner.scrub({"a": ["xSECRETy", ("SECRET",)], "b": 3}, "SECRET") == {
        "a": ["x[redacted]y", ["[redacted]"]],
        "b": 3,
    }
    assert runner.scrub("SECRET", None) == "SECRET"
    assert runner.result_text(None) == ""
    assert runner.result_text("plain") == "plain"
    assert runner.result_text([{"type": "text", "text": "a"}, {"type": "image"}, "x"]) == (
        "a\n[image]"
    )
    assert runner.result_text(42) == "42"


def test_error_messages_of_sdk_failures() -> None:
    from claude_agent_sdk import CLIConnectionError, CLIJSONDecodeError, ProcessError

    assert runner.error_of(ProcessError("x")) == runner.PROCESS.format(code="unknown")
    assert runner.error_of(CLIConnectionError("x")) == runner.NO_START
    assert runner.error_of(CLIJSONDecodeError("{", ValueError())) == (
        "The agent failed (CLIJSONDecodeError)."
    )
    assert runner.error_of(KeyError("x")) == runner.INTERNAL


def test_outcome_of_api_errors_and_missing_result() -> None:
    from claude_agent_sdk import ResultMessage

    from app.agents import definitions

    def result(**changes: Any) -> ResultMessage:
        base: dict[str, Any] = {
            "subtype": "success",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": True,
            "num_turns": 1,
            "session_id": "s",
        }
        return ResultMessage(**{**base, **changes})

    state = runner._State()
    assert runner.outcome_of(state, definitions.ECHO, None).error == runner.NO_RESULT
    for status, message in ((429, runner.BUSY), (529, runner.BUSY), (500, "HTTP 500")):
        state.result = result(api_error_status=status)
        assert message in (runner.outcome_of(state, definitions.ECHO, None).error or "")
    # A key without credit: the mark of the assistant message decides, whatever the status,
    # and the failure is the provider's, so a chain pauses instead of failing each document.
    for status in (400, 402, None):
        broke = runner._State(no_credit=True)
        broke.result = result(api_error_status=status)
        error = runner.outcome_of(broke, definitions.ECHO, None).error
        assert error == runner.NO_CREDIT
        assert runner.is_provider_failure(error) is True
        assert runner.is_box_failure(error) is False
    assert runner.is_provider_failure(runner.API_ERROR.format(status=400)) is False
    state.result = result(subtype="error_during_execution")
    assert runner.outcome_of(state, definitions.ECHO, None).error == (
        "The agent failed (error_during_execution)."
    )
    state.result = result(is_error=False, result=None, total_cost_usd=float("nan"))
    state.last_text = "last words"
    done = runner.outcome_of(state, definitions.ECHO, None)
    assert done.output == {"text": "last words"}
    assert done.cost_usd is None
    assert runner._cost(True) is None
    assert runner._cost("abc") is None
    assert runner._cost(-1) is None
    assert runner._minutes(600) == "10 minutes"
    assert runner._minutes(60) == "1 minute"
