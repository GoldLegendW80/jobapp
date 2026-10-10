"""What the Claude Agent SDK actually spawns (review of PR #21): its process start is replaced by
a recorder, so the real SubprocessCLITransport builds the command and the environment as it would
in the worker, and nothing runs. With the worker's settings there is exactly one process, the
run's Claude Code, and none of the worker's secrets reach it; without them the SDK's `claude -v`
probe would start first with the worker's environment unfiltered."""

import asyncio
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import anyio
import pytest
from claude_agent_sdk import CLINotFoundError

from app import worker
from app.agents import definitions, runner
from app.agents import tools as app_tools
from app.agents.backend import BackendRequest, SdkBackend
from app.agents.models import CredentialKind
from tests.agents.conftest import API_KEY

WORKER_SECRETS = {
    "DATABASE_URL": "postgresql+psycopg://jobapp:db-secret-pw@db:5432/jobapp",
    "CREDENTIALS_KEY": "credentials-key-secret",
    "CREDENTIALS_KEY_PREVIOUS": "previous-key-secret",
    "SECRET_KEY": "session-signing-secret",
    "POSTGRES_PASSWORD": "db-secret-pw",
    "REDIS_URL": "redis://:redis-secret@redis:6379/0",
    "TS_AUTHKEY": "tskey-auth-secret",
}
SKIP = "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"


@pytest.fixture
def secrets_in_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    # A copy of the environment for this test only: drop_secrets_from_environ deletes names
    # (TEST_DATABASE_URL, REDIS_URL of CI...) that the rest of the test session still needs.
    environ = dict(os.environ)
    environ.pop(SKIP, None)
    environ.update(WORKER_SECRETS)
    monkeypatch.setattr(os, "environ", environ)


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str] | None]]:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    async def record(cmd: list[str], **kwargs: Any) -> Any:
        calls.append((list(cmd), kwargs.get("env")))
        raise FileNotFoundError(cmd[0])  # stops the SDK right there: nothing ever runs

    monkeypatch.setattr(anyio, "open_process", record)
    return calls


def run_until_spawn(tmp_path: Path) -> None:
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    scratch = tmp_path / "scratch"
    for name in ("home", "config", "tmp", "work"):
        (scratch / name).mkdir(parents=True)

    async def gate(*_: Any) -> dict[str, Any]:
        return {}

    tools = app_tools.build(None, uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]
    options = runner.build_options(
        definitions.ECHO,
        tools,
        runner.build_env(CredentialKind.anthropic_api_key, API_KEY, scratch),
        scratch / "work",
        gate,
    )
    options.cli_path = str(cli)
    request = BackendRequest(prompt="hi", options=options, tools=tools, gate=gate)

    async def go() -> None:
        async for _ in SdkBackend().stream(request):
            pass  # pragma: no cover, the spawn fails first

    with pytest.raises(CLINotFoundError):
        asyncio.run(go())


def test_the_only_process_is_the_runs_and_it_gets_no_worker_secret(
    secrets_in_environ: None,
    spawned: list[tuple[list[str], dict[str, str] | None]],
    tmp_path: Path,
) -> None:
    worker.skip_sdk_version_probe()
    assert os.environ[SKIP] == "1"
    run_until_spawn(tmp_path)
    assert len(spawned) == 1, [cmd for cmd, _ in spawned]
    cmd, env = spawned[0]
    assert "-v" not in cmd
    assert cmd[1:3] == ["--output-format", "stream-json"]
    assert env is not None
    for name, value in WORKER_SECRETS.items():
        assert env.get(name, "") == "", name
        assert all(value not in v for v in env.values()), name
    assert env["ANTHROPIC_API_KEY"] == API_KEY


def test_once_the_worker_dropped_its_secrets_nothing_can_inherit_them(
    secrets_in_environ: None,
    spawned: list[tuple[list[str], dict[str, str] | None]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ["APP_ENV"] = "test"  # the test's own copy (fixture)
    dropped = worker.drop_secrets_from_environ()
    assert set(WORKER_SECRETS) <= set(dropped)
    assert "PATH" not in dropped
    assert "APP_ENV" not in dropped
    for name in WORKER_SECRETS:
        assert name not in os.environ
    # Even the probe, started without env=, would now inherit nothing secret.
    run_until_spawn(tmp_path)
    probe_cmd, probe_env = spawned[0]
    assert probe_cmd[1:] == ["-v"]
    assert probe_env is None  # inherits os.environ, which no longer holds a secret
    for name in WORKER_SECRETS:
        assert name not in os.environ


def test_without_the_skip_the_probe_would_inherit_the_worker_environment(
    secrets_in_environ: None,
    spawned: list[tuple[list[str], dict[str, str] | None]],
    tmp_path: Path,
) -> None:
    # Why the worker turns the probe off: it is spawned first, with no env= at all.
    run_until_spawn(tmp_path)
    probe_cmd, probe_env = spawned[0]
    assert probe_cmd[1:] == ["-v"]
    assert probe_env is None
    assert os.environ["SECRET_KEY"] == WORKER_SECRETS["SECRET_KEY"]
