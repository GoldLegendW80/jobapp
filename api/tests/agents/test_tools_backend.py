"""App tools (user scoping server side), the backend seam (the fake refused in production), the
recorded streams (every one parses with the SDK's own parser) and the definitions."""

import asyncio
import json
import uuid
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    UserMessage,
)
from sqlalchemy.orm import Session, sessionmaker

from app.agents import backend, definitions, fake_backend
from app.agents import tools as app_tools
from app.config import Settings
from tests.agents.run_helpers import add_job, add_user
from tests.conftest import make_settings


def call(tools: app_tools.AppTools, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(tools.by_name[name].handler(args))


def test_get_job_is_bound_to_the_runs_user(factory: sessionmaker[Session]) -> None:
    alice = add_user(factory, "alice@example.org")
    bob = add_user(factory, "bob@example.org")
    alices = add_job(factory, alice, "Acme", "Engineer", "Ünïcode déscription " + "x" * 7000)
    bobs = add_job(factory, bob, "Secret", "Hidden")
    tools = app_tools.build(factory, alice, ("get_job",))
    assert tools.allowed_names == ["mcp__app__get_job"]
    assert tools.server["type"] == "sdk"
    assert tools.server["name"] == "app"
    found = call(tools, "mcp__app__get_job", {"job_id": str(alices)})
    assert "is_error" not in found
    payload = json.loads(found["content"][0]["text"])
    assert payload["company"] == "Acme"
    assert payload["status"] == "found"
    assert payload["untrusted_description"].startswith("Ünïcode")
    assert payload["description_truncated"]
    assert len(payload["untrusted_description"]) == app_tools.DESCRIPTION_MAX
    assert "user_id" not in payload
    for wrong in (str(bobs), str(uuid.uuid4()), "not-a-uuid", "", None, 42):
        answer = call(tools, "mcp__app__get_job", {"job_id": wrong})
        assert answer == {"content": [{"type": "text", "text": "Job not found."}], "is_error": True}
    # Bob's own server reads his job.
    bobs_tools = app_tools.build(factory, bob, ("get_job",))
    assert (
        "Secret"
        in call(bobs_tools, "mcp__app__get_job", {"job_id": str(bobs)})["content"][0]["text"]
    )


def test_tool_schema_takes_no_user_id() -> None:
    tools = app_tools.build(None, uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]
    schema = tools.by_name["mcp__app__get_job"].input_schema
    assert isinstance(schema, dict)
    assert list(schema["properties"]) == ["job_id"]
    assert schema["additionalProperties"] is False


def test_unknown_tool_name_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown app tools: shell"):
        app_tools.build(None, uuid.uuid4(), ("get_job", "shell"))  # type: ignore[arg-type]


def test_fake_backend_is_refused_in_production(migrated_url: str) -> None:
    settings = make_settings(migrated_url)
    assert backend.for_settings(settings) is backend.SdkBackend
    fake = settings.model_copy(update={"agents_fake_backend": True})
    assert backend.for_settings(fake) is fake_backend.FakeBackend
    # Even a Settings object built around its validators cannot get the fake in production.
    sneaky = Settings.model_construct(
        **{**settings.model_dump(), "app_env": "production", "agents_fake_backend": True}
    )
    with pytest.raises(backend.FakeInProductionError):
        backend.for_settings(sneaky)


def test_settings_refuse_the_fake_in_production() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="AGENTS_FAKE_BACKEND"):
        Settings(
            database_url="postgresql+psycopg://u:p@db/x",  # type: ignore[arg-type]
            app_env="production",
            secret_key="s" * 40,  # type: ignore[arg-type]
            credentials_key="A" * 43 + "=",  # type: ignore[arg-type]
            agents_fake_backend=True,
        )


@pytest.mark.parametrize("name", fake_backend.scripts())
def test_every_recorded_stream_parses(name: str) -> None:
    steps = fake_backend.load_script(name)
    variables = {"model": "claude-opus-5-5", "session_id": "s", "message": "m", "job_id": "j"}
    seen = []
    for step in steps:
        assert set(step) <= {"sleep", "message", "raise"}, step
        if "message" in step:
            message = fake_backend.parse_message(fake_backend._fill(step["message"], variables))
            seen.append(type(message))
    if name != "missing_cli":
        assert seen[0] is SystemMessage
    if name not in ("crash", "missing_cli"):
        assert seen[-1] is ResultMessage
        complete = (SystemMessage, AssistantMessage, UserMessage, ResultMessage)
        # Only the recording of the CLI's partial messages holds stream events itself (issue #33).
        allowed = (*complete, StreamEvent) if name == "partial_recorded" else complete
        assert all(t in allowed for t in seen)
    if name == "partial_recorded":
        assert StreamEvent in seen


def test_unknown_or_unsafe_script_names() -> None:
    for name in ("nope", "../definitions", "echo.json", ""):
        with pytest.raises(fake_backend.UnknownScriptError):
            fake_backend.load_script(name)


def test_sdk_backend_reports_a_missing_cli(tmp_path: Any) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, CLINotFoundError

    sdk = backend.SdkBackend()
    asyncio.run(sdk.interrupt())  # nothing running: nothing to do
    request = backend.BackendRequest(
        prompt="x",
        options=ClaudeAgentOptions(cli_path=str(tmp_path / "no-claude"), cwd=str(tmp_path)),
        tools=None,  # type: ignore[arg-type]
        gate=None,  # type: ignore[arg-type]
    )
    assert "prompt" not in repr(request)
    assert "options" not in repr(request)

    async def go() -> None:
        async for _ in sdk.stream(request):
            pass  # pragma: no cover

    with pytest.raises(CLINotFoundError):
        asyncio.run(go())


def test_definitions() -> None:
    echo = definitions.get("echo", "development")
    assert echo.model == "claude-opus-5-5"
    assert echo.wall_time_s == 600
    assert echo.tools == ("get_job",)
    with pytest.raises(definitions.UnknownAgentError):
        definitions.get("echo", "production")
    with pytest.raises(definitions.UnknownAgentError):
        definitions.get("nope", "development")
    assert echo.validate_input({"message": "hi", "script": None}) == {"message": "hi"}
    job = uuid.uuid4()
    assert echo.prompt({"message": "hi"}, job) == f"hi\n\nJob id: {job}"
    assert echo.prompt({"message": "hi"}, None) == "hi"
