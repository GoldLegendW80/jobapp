"""A model per kind of credential (ingestion G6, point 5; decision 5 of the owner): an agent
that names one runs on `claude-opus-5-5` with a subscription and on `claude-sonnet-5-5` with an
API key, chosen when the worker reads the credential and written on the run with its kind. The
agents of the product name none and stay on their model whatever the credential."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, RunStatus
from app.config import Settings
from tests.agents.conftest import API_KEY, OAUTH, OTHER_KEY
from tests.agents.g6_helpers import (
    Streams,
    agent,
    assistant,
    g6_tools,
    init,
    run_context,
    streams,
    text,
)
from tests.agents.run_helpers import (
    Backends,
    add_job,
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

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
# As the ingestion plan writes its two agents.
BY_KIND = agent("g6_model", model=OPUS, model_by_credential={"anthropic_api_key": SONNET})
ANSWER = [init(), assistant("m1", text("Done."))]
SUBSCRIPTION = CredentialKind.claude_subscription
KEY = CredentialKind.anthropic_api_key


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def user(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


def created_with(
    factory: sessionmaker[Session], user: uuid.UUID, model: str, data: dict[str, Any] | None = None
) -> uuid.UUID:
    """A run of the agent as its module creates it: with the model it believed in then."""
    with factory() as db, db.begin():
        return runs.create_run(db, user, "g6_model", None, data or {}, model, now()).id


@pytest.mark.parametrize(
    ("kind", "secret", "model"),
    [(KEY, API_KEY, SONNET), (SUBSCRIPTION, OAUTH, OPUS)],
    ids=["an API key", "a subscription"],
)
def test_the_run_uses_the_model_of_its_credential(
    kind: CredentialKind,
    secret: str,
    model: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    save_credential(factory, keyring, user, secret, kind)
    streams.add("g6_model", ANSWER)
    backends = Backends()
    run_id = created_with(factory, user, "written-at-creation")
    rc = run_context(settings, factory, keyring, BY_KIND, backends=backends)
    assert execute(rc, run_id) is RunStatus.done
    # What Claude Code was started with, what it said it ran on, and what the run keeps.
    assert backends.made[0].seen_options.model == model
    started = next(data for name, data in events(factory, run_id) if name == "init")
    assert started["model"] == model
    row = run_row(factory, run_id)
    assert (row.model, row.credential_kind) == (model, kind)


def test_the_command_line_names_the_chosen_model(tmp_path: Path) -> None:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    tools = app_tools.build(None, uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]

    async def gate(*_: Any) -> dict[str, Any]:
        return {}

    for kind, model in ((KEY, SONNET), (SUBSCRIPTION, OPUS)):
        options = runner.build_options(
            BY_KIND, tools, {}, tmp_path, gate, model=BY_KIND.model_for(kind)
        )
        transport = SubprocessCLITransport(prompt="x", options=options)
        transport._cli_path = "/usr/bin/claude"
        command = transport._build_command()
        assert command[command.index("--model") + 1] == model
    # Without one the definition's own model, as before this task.
    assert runner.build_options(BY_KIND, tools, {}, tmp_path, gate).model == OPUS


@pytest.mark.parametrize(
    ("first", "then", "model"),
    [
        ((SUBSCRIPTION, OAUTH), (KEY, OTHER_KEY), SONNET),
        ((KEY, API_KEY), (SUBSCRIPTION, OAUTH), OPUS),
    ],
    ids=["a subscription replaced by a key", "a key replaced by a subscription"],
)
def test_the_credential_replaced_between_the_creation_and_the_start(
    first: tuple[CredentialKind, str],
    then: tuple[CredentialKind, str],
    model: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The run was created, and its model written, for the credential the user had then. The
    worker reads another one: the model follows the credential actually used."""
    save_credential(factory, keyring, user, first[1], first[0])
    run_id = created_with(factory, user, BY_KIND.model_for(first[0]))
    assert run_row(factory, run_id).model != model
    save_credential(factory, keyring, user, then[1], then[0])
    streams.add("g6_model", ANSWER)
    backends = Backends()
    rc = run_context(settings, factory, keyring, BY_KIND, backends=backends)
    assert execute(rc, run_id) is RunStatus.done
    assert backends.made[0].seen_options.model == model
    row = run_row(factory, run_id)
    assert (row.model, row.credential_kind) == (model, then[0])


def test_the_model_is_written_with_the_kind_before_the_run_goes_anywhere(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A run that never hears from Claude Code (it dies at once, or the worker stops) still
    says which model it was started on."""
    save_credential(factory, keyring, user)
    streams.add("g6_model", [{"raise": "cli_not_found"}])
    died = created_with(factory, user, OPUS)
    assert execute(run_context(settings, factory, keyring, BY_KIND), died) is RunStatus.failed
    assert (run_row(factory, died).model, run_row(factory, died).error) == (SONNET, runner.NO_CLI)

    streams.add("g6_model", [{"sleep": 30}])
    stopped = created_with(factory, user, OPUS)
    rc = run_context(settings, factory, keyring, BY_KIND, backends=Backends(speed=1.0))
    seen: list[tuple[str | None, CredentialKind | None]] = []

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, stopped))
        while not rc.backend_factory.made:  # type: ignore[attr-defined]  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        # While the run is running: the kind and the model, written together.
        row = run_row(factory, stopped)
        seen.append((row.model, row.credential_kind))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert seen == [(SONNET, KEY)]
    row = run_row(factory, stopped)
    assert (row.model, row.error) == (SONNET, runs.STOPPED)


def test_without_a_credential_the_run_keeps_the_model_it_was_created_with(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    streams.add("g6_model", ANSWER)
    run_id = created_with(factory, user, OPUS)
    assert execute(run_context(settings, factory, keyring, BY_KIND), run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert (row.error, row.model, row.credential_kind) == (runner.NO_CREDENTIAL, OPUS, None)


# --- the agents of the product keep their model ---------------------------------------------------


@pytest.mark.parametrize("name", ["echo", "triage", "job_chat"])
def test_the_products_agents_name_no_model_per_credential(name: str) -> None:
    found = definitions.AGENTS[name]
    assert found.model_by_credential is None
    for kind in (KEY, SUBSCRIPTION, "anthropic_api_key", "claude_subscription", None):
        assert found.model_for(kind) == OPUS


@pytest.mark.parametrize(
    ("kind", "secret"), [(KEY, API_KEY), (SUBSCRIPTION, OAUTH)], ids=["key", "subscription"]
)
@pytest.mark.parametrize("name", ["echo", "triage"])
def test_a_product_agent_runs_on_opus_whatever_the_credential(
    name: str,
    kind: CredentialKind,
    secret: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
) -> None:
    save_credential(factory, keyring, user, secret, kind)
    job = add_job(factory, user)
    data = {"message": "hi"} if name == "echo" else {}
    run_id = new_run(factory, user, data, job_id=job, agent=name)
    backends = Backends()
    rc = run_context(settings, factory, keyring, backends=backends)
    execute(rc, run_id)
    assert backends.made[0].seen_options.model == OPUS
    row = run_row(factory, run_id)
    assert (row.model, row.credential_kind) == (OPUS, kind)


def test_reading_the_credential_does_not_touch_the_model_of_a_product_agent(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, user: uuid.UUID
) -> None:
    """For an agent that names no model per kind the worker writes the kind alone, as it did
    (PR #71): the model column is only written when the run ends."""
    save_credential(factory, keyring, user)
    with factory() as db, db.begin():
        run_id = runs.create_run(db, user, "echo", None, {"message": "x"}, "as-created", now()).id
    rc = run_context(settings, factory, keyring)
    found = runner._reveal(rc, user, run_id, definitions.ECHO)
    assert not isinstance(found, str)
    row = run_row(factory, run_id)
    assert (row.model, row.credential_kind) == ("as-created", KEY)
    found = runner._reveal(rc, user, run_id, BY_KIND)
    assert run_row(factory, run_id).model == SONNET


# --- the definition -------------------------------------------------------------------------------


def test_model_for() -> None:
    assert BY_KIND.model_for(KEY) == BY_KIND.model_for("anthropic_api_key") == SONNET
    assert BY_KIND.model_for(SUBSCRIPTION) == BY_KIND.model_for("claude_subscription") == OPUS
    assert BY_KIND.model_for(None) == OPUS  # not known yet: the default
    both = agent("g6_model", model="m0", model_by_credential={KEY: "m1", SUBSCRIPTION: "m2"})
    assert (both.model_for(KEY), both.model_for(SUBSCRIPTION), both.model_for(None)) == (
        "m1",
        "m2",
        "m0",
    )
    with pytest.raises(ValueError, match="password"):
        BY_KIND.model_for("password")
    assert agent("g6_model", model_by_credential={}).model_for(KEY) == OPUS
    assert hash(BY_KIND) == hash(BY_KIND)  # a mapping among its fields does not break hashing


@pytest.mark.parametrize("kind", ["anthropic_apikey", "api_key", "", "ANTHROPIC_API_KEY"])
def test_a_misspelt_kind_is_refused_when_the_definition_is_made(kind: str) -> None:
    """It would silently leave those runs on the default, dearer model."""
    with pytest.raises(ValueError, match="unknown credential kind"):
        agent("g6_model", model_by_credential={kind: SONNET})


def test_record_credential_kind_with_and_without_a_model(
    factory: sessionmaker[Session], user: uuid.UUID
) -> None:
    run_id = new_run(factory, user)
    with factory() as db, db.begin():
        runs.record_credential_kind(db, run_id, SUBSCRIPTION)
    assert (run_row(factory, run_id).model, run_row(factory, run_id).credential_kind) == (
        OPUS,
        SUBSCRIPTION,
    )
    with factory() as db, db.begin():
        runs.record_credential_kind(db, run_id, KEY, SONNET)
    assert (run_row(factory, run_id).model, run_row(factory, run_id).credential_kind) == (
        SONNET,
        KEY,
    )
