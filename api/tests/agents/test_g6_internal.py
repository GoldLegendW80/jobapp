"""Internal agents and the two registries (ingestion G6, points 1 and 8): an agent another
module registers, which only that module can start. Through every public route it is an agent
that does not exist; through its module it runs; its owner can follow, list and cancel its runs
like any other, and nobody else can."""

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    TOOLS,
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
from tests.agents.run_helpers import add_job, events, execute, new_run, run_row, save_credential
from tests.agents.test_runs_api import RUNS, RecordingQueue, recorded, save_key, user_id
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

__all__ = ["g6_tools", "recorded", "streams"]

INTERNAL = agent("g6_internal", internal=True)
UNKNOWN = "agent: no such agent here."


@pytest.fixture
def internal(monkeypatch: pytest.MonkeyPatch) -> definitions.AgentDefinition:
    """The internal agent, known to this process as after its module's import."""
    monkeypatch.setitem(definitions.AGENTS, INTERNAL.name, INTERNAL)
    return INTERNAL


# --- through the public routes --------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"agent": "g6_internal", "input": {}},
        {"agent": "g6_internal", "input": {"message": "start it"}},
        {"agent": "g6_internal", "input": {"token_budget": 100000, "step_id": "x"}},
        {"agent": "g6_internal", "input": {"script": "echo"}},
    ],
)
def test_post_runs_answers_422_as_for_an_agent_that_does_not_exist(
    api: Api,
    alice: str,
    recorded: RecordingQueue,
    internal: definitions.AgentDefinition,
    factory: sessionmaker[Session],
    body: dict[str, Any],
) -> None:
    save_key(api, alice)
    response = api.post(RUNS, token=alice, json=body)
    assert response.status_code == 422, response.text
    # The very words of an unknown agent: the route does not say the agent exists.
    assert response.json()["detail"] == UNKNOWN
    assert api.post(RUNS, token=alice, json={**body, "agent": "nope"}).json()["detail"] == UNKNOWN
    assert recorded.jobs == []
    with factory() as db:
        assert db.query(AgentRun).count() == 0


def test_post_runs_with_a_job_is_refused_too(
    api: Api,
    alice: str,
    recorded: RecordingQueue,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whatever else the definition says (a job, a chat): internal comes first.
    save_key(api, alice)
    job = add_job(factory, user_id(api, alice))
    for changes in ({"needs_job": True}, {"needs_job": True, "chat": True}, {"test_only": True}):
        found = replace(INTERNAL, **changes)  # type: ignore[arg-type]
        monkeypatch.setitem(definitions.AGENTS, found.name, found)
        response = api.post(
            RUNS, token=alice, json={"agent": found.name, "job_id": str(job), "input": {}}
        )
        assert response.status_code == 422
        assert response.json()["detail"] == UNKNOWN
    assert recorded.jobs == []


def test_the_agents_list_does_not_show_it(
    api: Api, alice: str, internal: definitions.AgentDefinition
) -> None:
    names = [item["name"] for item in api.get("/api/agents", token=alice).json()["items"]]
    assert names == ["echo", "triage"]


def test_the_chat_route_cannot_be_given_an_agent(
    api: Api,
    alice: str,
    recorded: RecordingQueue,
    internal: definitions.AgentDefinition,
    factory: sessionmaker[Session],
) -> None:
    """The other route that starts a run: it names no agent, and refuses to be given one."""
    save_key(api, alice)
    job = add_job(factory, user_id(api, alice))
    thread = api.post("/api/agents/threads", token=alice, json={"job_id": str(job)}).json()
    path = f"/api/agents/threads/{thread['id']}/messages"
    refused = api.post(path, token=alice, json={"text": "hello", "agent": "g6_internal"})
    assert refused.status_code == 422
    assert recorded.jobs == []
    sent = api.post(path, token=alice, json={"text": "hello"})
    assert sent.status_code == 202, sent.text
    assert sent.json()["run"]["agent"] == "job_chat"


def test_no_other_route_lets_a_client_name_an_agent(app: FastAPI) -> None:
    """Every public route, read from the app's own description: the only one whose body can
    name an agent is POST /api/agents/runs, tested above. (`agent` on GET /api/agents/runs is a
    filter on the caller's own runs.) And the chat's run names its agent itself, a constant."""
    import inspect

    from app.agents import chat

    schema = app.openapi()
    naming = []
    for path, methods in schema["paths"].items():
        for method, described in methods.items():
            content = described.get("requestBody", {}).get("content", {})
            for body in content.values():
                ref = body.get("schema", {}).get("$ref", "")
                model = schema["components"]["schemas"].get(ref.rsplit("/", 1)[-1], {})
                if "agent" in model.get("properties", {}):
                    naming.append(f"{method.upper()} {path}")
    assert naming == ["POST /api/agents/runs"]
    assert "AGENT.name" in inspect.getsource(chat.send)
    assert chat.AGENT is definitions.JOB_CHAT


def test_in_production_it_is_refused_the_same(
    migrated_url: str, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(definitions.AGENTS, INTERNAL.name, INTERNAL)
    assert definitions.get("g6_internal", "production") is INTERNAL  # the worker finds it
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable("g6_internal", "production")
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable("g6_internal", "test")
    with pytest.raises(definitions.UnknownAgentError):
        definitions.startable("echo", "production")  # a test agent, as before
    assert definitions.startable("triage", "production") is definitions.TRIAGE
    assert [found.name for found in definitions.listed("production")] == ["triage"]
    assert [found.name for found in definitions.listed("test")] == ["echo", "triage"]


# --- through its module ---------------------------------------------------------------------------


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


def test_started_by_its_module_it_runs(
    api: Api,
    alice: str,
    bob: str,
    internal: definitions.AgentDefinition,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The module's own path: runs.create_run, then the worker. The run ends done, and its
    owner reads it, lists it and finds it under its agent's name; another user finds nothing."""
    me = user_id(api, alice)
    save_credential(factory, keyring, me)
    streams.add(
        "g6_internal",
        [
            init(),
            assistant("m1", call("t1", "g6_write", text="kept")),
            assistant("m2", text("Ok.")),
        ],
    )
    run_id = new_run(factory, me, {}, agent="g6_internal")
    assert execute(run_context(settings, factory, keyring, internal), run_id) is RunStatus.done
    assert run_row(factory, run_id).output == {"text": "Ok."}
    body = api.get(f"{RUNS}/{run_id}", token=alice).json()
    assert (body["agent"], body["status"]) == ("g6_internal", "done")
    listed = api.get(RUNS, token=alice, params={"agent": "g6_internal"}).json()["items"]
    assert [item["id"] for item in listed] == [str(run_id)]
    assert api.get(f"{RUNS}/{run_id}", token=bob).status_code == 404
    assert api.get(RUNS, token=bob, params={"agent": "g6_internal"}).json()["items"] == []
    assert api.get(f"{RUNS}/{run_id}/stream", token=bob).status_code == 404


def test_its_owner_can_cancel_a_queued_run_of_it(
    api: Api,
    alice: str,
    bob: str,
    internal: definitions.AgentDefinition,
    factory: sessionmaker[Session],
) -> None:
    run_id = new_run(factory, user_id(api, alice), {}, agent="g6_internal")
    assert api.post(f"{RUNS}/{run_id}/cancel", token=bob).status_code == 404
    response = api.post(f"{RUNS}/{run_id}/cancel", token=alice)
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    assert events(factory, run_id)[-1] == ("status", {"status": "cancelled"})


def test_an_internal_agent_the_worker_does_not_know_fails_cleanly(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, api: Api, alice: str
) -> None:
    """The api registered it, this worker did not import its module."""
    run_id = new_run(factory, user_id(api, alice), {}, agent="g6_internal")
    assert execute(run_context(settings, factory, keyring), run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.UNKNOWN_AGENT


# --- definitions.register -------------------------------------------------------------------------


@pytest.fixture
def clean_registry() -> Any:
    before = dict(definitions.AGENTS)
    yield
    definitions.AGENTS.clear()
    definitions.AGENTS.update(before)


def test_register_adds_an_agent_and_takes_the_same_one_twice(clean_registry: None) -> None:
    found = agent("g6_registered", internal=True, private_io=True)
    assert definitions.register(found) is found
    assert definitions.AGENTS["g6_registered"] is found
    assert definitions.get("g6_registered", "production") is found
    assert definitions.live_text("g6_registered") is False
    # A module imported twice builds an equal definition: fine.
    again = agent("g6_registered", internal=True, private_io=True)
    assert definitions.register(again) is again
    assert list(definitions.AGENTS).count("g6_registered") == 1


def test_register_refuses_another_agent_under_a_taken_name(clean_registry: None) -> None:
    definitions.register(agent("g6_registered", internal=True))
    with pytest.raises(ValueError, match="already registered"):
        definitions.register(agent("g6_registered", internal=False))
    with pytest.raises(ValueError, match="already registered"):
        definitions.register(replace(definitions.ECHO, max_turns=99))
    assert definitions.AGENTS["echo"] is definitions.ECHO  # the product's agent is untouched
    assert definitions.register(definitions.TRIAGE) is definitions.TRIAGE


@pytest.mark.parametrize("name", ["", "G6", "g6 agent", "6g", "g6-agent", "é", "a" * 65, "../x"])
def test_register_refuses_a_name_that_is_not_one(clean_registry: None, name: str) -> None:
    with pytest.raises(ValueError, match="not an agent name"):
        definitions.register(agent(name))
    assert name not in definitions.AGENTS


def test_a_name_of_64_characters_is_the_longest(clean_registry: None) -> None:
    assert definitions.register(agent("a" * 64)).name == "a" * 64


# --- tools.register -------------------------------------------------------------------------------


def test_tools_register_adds_a_tool_a_definition_can_name(g6_tools: None) -> None:
    for name in TOOLS:
        assert app_tools.FACTORIES[name] is TOOLS[name]
    built = app_tools.build(None, uuid.uuid4(), ("g6_read", "get_job"))  # type: ignore[arg-type]
    assert built.allowed_names == ["mcp__app__g6_read", "mcp__app__get_job"]
    # The same factory again is fine, another one under the name is not.
    app_tools.register("g6_read", TOOLS["g6_read"])
    with pytest.raises(ValueError, match="already registered"):
        app_tools.register("g6_read", TOOLS["g6_write"])
    with pytest.raises(ValueError, match="already registered"):
        app_tools.register("get_job", TOOLS["g6_read"])
    assert app_tools.FACTORIES["g6_read"] is TOOLS["g6_read"]


@pytest.mark.parametrize("name", ["", "Read", "g6 read", "9lives", "mcp__app__x" * 5, "a-b", "é"])
def test_tools_register_refuses_a_name_that_is_not_one(name: str) -> None:
    with pytest.raises(ValueError, match="not a tool name"):
        app_tools.register(name, TOOLS["g6_read"])
    assert name not in app_tools.FACTORIES


def test_a_tool_registered_under_another_name_than_its_own_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allow list is made of the tools' own names: a factory registered as `g6_other` that
    builds `g6_read` would put a name on the allow list that no definition asked for."""
    monkeypatch.setitem(app_tools.FACTORIES, "g6_other", TOOLS["g6_read"])
    with pytest.raises(ValueError, match="registered under another name: g6_other"):
        app_tools.build(None, uuid.uuid4(), ("g6_other",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown app tools: g6_missing"):
        app_tools.build(None, uuid.uuid4(), ("g6_missing",))  # type: ignore[arg-type]


def test_the_products_agents_are_neither_internal_nor_private_nor_bound() -> None:
    for found in (definitions.ECHO, definitions.TRIAGE, definitions.JOB_CHAT):
        assert found.internal is False
        assert found.private_io is False
        assert found.bind is None
        assert found.on_final is None
        assert found.model_by_credential is None
        assert found.live is True
        assert found.model == "claude-opus-5-5"
    assert runs.ACTIVE_PER_USER == 4
