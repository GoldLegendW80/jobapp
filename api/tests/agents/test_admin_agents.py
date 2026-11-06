"""The Agents tab of Admin (agents A6): /api/admin/agents against a real Postgres, the overrides a
save writes, the runner using the latest version at the next run on the fake backend, and
migration 0031."""

import asyncio
import dataclasses
import json
import logging
import re
import secrets
import subprocess
import sys
import threading
import typing
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.agents import catalog, definitions, overrides, runner
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentOverride, AgentRun, CredentialKind, RunStatus
from app.config import Settings
from app.identity.models import Role, User
from tests.agents.conftest import OAUTH
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    execute,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.agents.test_runs_migration import at_head
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, make_user
from tests.test_migrations import alembic_config

__all__ = ["at_head"]

AGENTS = "/api/admin/agents"
API_DIR = Path(__file__).resolve().parents[2]
APP_DIR = API_DIR / "app"


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def admin(factory: sessionmaker[Session], api: Api) -> str:
    make_user(factory, email="e2e-admin@example.org", name="Ada", role=Role.admin)
    return api.login_token("e2e-admin@example.org")


@pytest.fixture
def member(factory: sessionmaker[Session], api: Api) -> str:
    make_user(factory, email="e2e-member@example.org", name="Mia", role=Role.member)
    return api.login_token("e2e-member@example.org")


def save(api: Api, token: str, name: str, changes: dict[str, Any], **body: Any) -> Any:
    return api.post(f"{AGENTS}/{name}/versions", token=token, json={"changes": changes, **body})


def versions(factory: sessionmaker[Session], agent: str) -> list[AgentOverride]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(AgentOverride)
                .where(AgentOverride.agent == agent)
                .order_by(AgentOverride.version)
            )
        )


# --- every agent renders ---------------------------------------------------------------------


def test_every_module_that_registers_an_agent_is_reached_by_the_catalog() -> None:
    """A new agent in a module the catalog does not import would be missing from the tab."""
    catalog.load()
    registering = set()
    for path in APP_DIR.rglob("*.py"):
        if "definitions.register(" in path.read_text(encoding="utf-8"):
            dotted = ".".join(path.relative_to(APP_DIR.parent).with_suffix("").parts)
            registering.add(dotted)
    registering.discard("app.agents.definitions")
    assert registering, "the scan found no module"
    for name in registering:
        assert name in sys.modules, f"{name} registers an agent the catalog does not load"
    known = {definitions.module_of(name) for name in definitions.AGENTS}
    assert registering <= known


def test_the_list_and_the_page_render_for_every_registered_agent(api: Api, admin: str) -> None:
    agents = catalog.load()
    listed = api.get(AGENTS, token=admin)
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "no-store"
    names = [item["name"] for item in listed.json()["items"]]
    assert sorted(names) == sorted(agents)  # test agents too, outside production
    for name, agent in agents.items():
        page = api.get(f"{AGENTS}/{name}", token=admin)
        assert page.status_code == 200, (name, page.text)
        body = page.json()
        assert body["code"]["system_prompt"] == agent.system_prompt
        assert [tool["name"] for tool in body["tools"]] == list(agent.tools)
        assert body["module"] == definitions.module_of(name) != ""
        assert body["first_message"]
        assert body["starter"]
        assert body["guards"]
        # The person's own never apply rules, and no rule of the app's own about a kind of role
        # (owner, 2026-11-05).
        assert "the person's own never apply rules" in body["guards"]
        assert "cyber" not in body["guards"].lower()
        assert {field["name"] for field in body["input_fields"]} == set(
            agent.input_model.model_fields
        )
        assert body["claude_code"]["permission_mode"] == "dontAsk"
        assert body["claude_code"]["setting_sources"] == []
        assert "Skill" in body["claude_code"]["disallowed_tools"]
        # Hard rule 1: no dash used as a separator in what the page says.
        for text in (body["starter"], body["first_message"], *body["flags"], *body["ends"]):
            assert not re.search(r"[–—]| - ", text), (name, text)


def test_a_described_class_says_what_its_first_message_is_built_from() -> None:
    agents = catalog.load()
    for name in ("browser_discovery", "apply_form", "apply_answer", "documents_cv", "triage"):
        text = catalog.first_message(agents[name])
        assert "Built per run by" not in text, name
    assert "search_scope" in catalog.first_message(agents["browser_discovery"])
    assert "{FIRST_MESSAGE}" not in catalog.first_message(agents["apply_form"])


def test_the_tab_hides_test_agents_in_production(
    api: Api, admin: str, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = app.state.settings.model_copy(update={"app_env": "production"})
    monkeypatch.setattr(app.state, "settings", production)
    names = [item["name"] for item in api.get(AGENTS, token=admin).json()["items"]]
    assert "echo" not in names
    assert "triage" in names
    assert api.get(f"{AGENTS}/echo", token=admin).status_code == 404


CATEGORY_KEYS = ["finding", "applying", "documents", "profile", "jobs", "test"]


def test_every_registered_agent_has_a_category_from_the_fixed_list() -> None:
    agents = catalog.load()
    assert [category.key for category in catalog.CATEGORIES] == CATEGORY_KEYS
    assert [category.title for category in catalog.CATEGORIES] == [
        "Finding jobs",
        "Applying",
        "Documents",
        "Profile and import",
        "Jobs and chat",
        "Test",
    ]
    for category in catalog.CATEGORIES:
        # Hard rule 1: no dash used as a separator in a heading or its sentence.
        assert not re.search(r"[–—]| - ", category.title + category.sentence), category
        assert category.sentence.endswith(".")
    # The app's own agents: a test elsewhere may register one of its own for a while.
    product = {name for name in agents if definitions.module_of(name).startswith("app.")}
    assert len(product) >= 18, sorted(product)
    for name in product:
        assert catalog.CATEGORY_OF.get(name) in CATEGORY_KEYS, f"{name} has no category"
        assert catalog.category_of(agents[name]) == catalog.CATEGORY_OF[name]
        # The test agents, and only they, are in Test (hidden in production with them).
        assert (catalog.CATEGORY_OF[name] == "test") == agents[name].test_only, name
    assert set(catalog.CATEGORY_OF) <= set(agents), "a category names an agent that is gone"
    expected = {
        "browser_discovery": "finding",
        "browser_finder": "finding",
        "apply_form": "applying",
        "apply_answer": "applying",
        "apply_check": "applying",
        "documents_cv": "documents",
        "documents_chat_letter": "documents",
        "document_transcribe": "profile",
        "profile_extract": "profile",
        "triage": "jobs",
        "job_chat": "jobs",
        "echo": "test",
    }
    assert {name: catalog.CATEGORY_OF[name] for name in expected} == expected


def test_an_agent_missing_from_the_categories_goes_with_its_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unlisted = dataclasses.replace(definitions.TRIAGE, name="a8_unlisted")
    assert "a8_unlisted" not in catalog.CATEGORY_OF
    assert catalog.category_of(unlisted) == "other"  # no module known
    monkeypatch.setitem(definitions._MODULES, "a8_unlisted", "app.browser.finder")
    assert catalog.category_of(unlisted) == "finding"
    monkeypatch.setitem(definitions._MODULES, "a8_unlisted", "app.nowhere.thing")
    assert catalog.category_of(unlisted) == "other"  # a package the map does not know
    assert "other" not in CATEGORY_KEYS  # so the page lists it under Other
    assert catalog.category_of(dataclasses.replace(definitions.ECHO, name="a8_test")) == "test"


def test_the_list_returns_each_agents_category_and_the_groups_in_order(
    api: Api, admin: str, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = api.get(AGENTS, token=admin).json()
    assert [category["key"] for category in body["categories"]] == CATEGORY_KEYS
    assert body["categories"][0] == {
        "key": "finding",
        "title": "Finding jobs",
        "sentence": catalog.CATEGORIES[0].sentence,
    }
    category = {item["name"]: item["category"] for item in body["items"]}
    assert category["browser_discovery"] == "finding"
    assert category["profile_extract"] == "profile"
    assert category["echo"] == "test"
    assert set(category.values()) <= set(CATEGORY_KEYS)
    production = app.state.settings.model_copy(update={"app_env": "production"})
    monkeypatch.setattr(app.state, "settings", production)
    body = api.get(AGENTS, token=admin).json()
    assert "test" not in {item["category"] for item in body["items"]}
    assert [category["key"] for category in body["categories"]] == CATEGORY_KEYS


def test_only_an_admin_reaches_the_tab(
    api: Api, member: str, factory: sessionmaker[Session]
) -> None:
    for method, path in (
        ("GET", AGENTS),
        ("GET", f"{AGENTS}/triage"),
        ("POST", f"{AGENTS}/triage/versions"),
    ):
        # 404, not 403: require_admin's rule, a member learns nothing about admin routes.
        answer = api.request(method, path, token=member, json={"changes": {}})
        assert answer.status_code == 404, (method, path)
        assert api.request(method, path, json={"changes": {}}).status_code == 401
    assert versions(factory, "triage") == []


# --- saving --------------------------------------------------------------------------------------


def test_a_save_stores_only_what_differs_from_the_code(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    code = definitions.TRIAGE
    answer = save(
        api,
        admin,
        "triage",
        {"system_prompt": "Be brief.", "max_turns": code.max_turns, "effort": "high"},
        note="  shorter  ",
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["version"] == 1
    assert body["saved"] == {"system_prompt": "Be brief.", "effort": "high"}
    assert body["effective"]["system_prompt"] == "Be brief."
    assert body["code"]["system_prompt"] == code.system_prompt
    [row] = versions(factory, "triage")
    assert row.changes == {"system_prompt": "Be brief.", "effort": "high"}
    assert row.note == "shorter"
    assert body["versions"][0]["saved_by"] == "Ada"
    assert body["versions"][0]["fields"] == ["effort", "system_prompt"]


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        ({"system_prompt": ""}, "system_prompt"),
        ({"system_prompt": "   \n "}, "system_prompt"),
        ({"system_prompt": "x" * (overrides.PROMPT_MAX + 1)}, "system_prompt"),
        ({"model": "gpt-5"}, "model"),
        ({"model_by_credential": {"anthropic_api_key": "claude-2"}}, "model_by_credential"),
        ({"model_by_credential": {"a_password": "claude-opus-5-5"}}, "model_by_credential"),
        ({"effort": "extreme"}, "effort"),
        ({"max_turns": 0}, "max_turns"),
        ({"wall_time_s": 1}, "wall_time_s"),
        ({"tools": ["get_job", "send_mail"]}, "tools"),  # never a tool the code did not give
        ({"private_io": False}, "private_io"),  # the flags are not editable
        ({"input_model": "x"}, "input_model"),
    ],
)
def test_a_change_the_agent_cannot_take_is_refused_and_nothing_is_written(
    api: Api, admin: str, factory: sessionmaker[Session], changes: dict[str, Any], where: str
) -> None:
    answer = save(api, admin, "triage", changes)
    assert answer.status_code == 422, answer.text
    assert where in answer.json()["detail"]
    assert versions(factory, "triage") == []


def test_a_save_on_a_version_the_page_did_not_show_is_refused(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    assert save(api, admin, "triage", {"effort": "high"}).status_code == 201
    stale = save(api, admin, "triage", {"effort": "low"})  # the page showed no version
    assert stale.status_code == 409
    assert len(versions(factory, "triage")) == 1
    assert save(api, admin, "triage", {"effort": "low"}, base_version=1).status_code == 201
    assert save(api, admin, "nobody", {}).status_code == 404


def test_two_saves_at_once_write_one_version(factory: sessionmaker[Session]) -> None:
    """The second save waits for the first one's lock, reads again, and finds it stale."""
    user = make_user(factory, email="e2e-admin@example.org").id
    first = factory()
    overrides.save(
        first, definitions.TRIAGE, {"effort": "high"}, base_version=None, note=None,
        user_id=user, now=now(),
    )  # fmt: skip
    outcome: list[BaseException | None] = []

    def second() -> None:
        with factory() as db:
            try:
                overrides.save(
                    db, definitions.TRIAGE, {"effort": "low"}, base_version=None, note=None,
                    user_id=user, now=now(),
                )  # fmt: skip
                db.commit()
                outcome.append(None)
            except overrides.StaleVersionError as exc:
                outcome.append(exc)

    thread = threading.Thread(target=second)
    thread.start()
    first.commit()
    first.close()
    thread.join()
    assert isinstance(outcome[0], overrides.StaleVersionError)
    assert [row.changes for row in versions(factory, "triage")] == [{"effort": "high"}]


def test_the_efforts_offered_are_the_ones_the_sdk_takes() -> None:
    sdk_types = pytest.importorskip("claude_agent_sdk.types")
    assert typing.get_args(sdk_types.EffortLevel) == definitions.EFFORTS


def test_a_default_message_is_editable_only_where_a_first_message_reads_it(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    """Saved for an agent whose first message is built in code, it would reach no run: 422."""
    agents = catalog.load()
    canary = f"canary-{secrets.token_hex(8)}"
    used, refused = set(), set()
    for name, agent in agents.items():
        page = api.get(f"{AGENTS}/{name}", token=admin).json()
        assert page["uses_default_message"] is agent.uses_default_message, name
        # The class's own prompt says whether a run reads the default message, not the flag.
        planted = dataclasses.replace(agent, default_message=canary)
        try:
            text = planted.prompt({}, None)
        except Exception:
            text = ""
        if not agent.chat:
            assert (canary in text) is agent.uses_default_message, name
        answer = save(api, admin, name, {"default_message": "Search only remote jobs."})
        if agent.uses_default_message:
            assert answer.status_code == 201, (name, answer.text)
            assert answer.json()["saved"] == {"default_message": "Search only remote jobs."}
            used.add(name)
        else:
            assert answer.status_code == 422, (name, answer.text)
            assert answer.json()["detail"] == (
                "default_message: this agent builds its first message in code"
            )
            assert versions(factory, name) == [], name
            # The code's own value is no change, as for every field.
            same = save(api, admin, name, {"default_message": agent.default_message})
            assert same.status_code == 201, (name, same.text)
            assert same.json()["saved"] == {}
            refused.add(name)
    assert {"triage", "echo"} <= used
    assert {"browser_discovery", "job_chat", "apply_form", "documents_cv"} <= refused
    # A version saved before that rule still runs the code's value there.
    finder = agents["browser_discovery"]
    assert overrides.apply(finder, {"default_message": "Remote only."}) is finder


def test_a_saved_default_message_is_the_first_message_of_the_next_run(
    api: Api,
    admin: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
) -> None:
    message = "Assess this posting for a remote role only."
    assert save(api, admin, "triage", {"default_message": message}).status_code == 201
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user)
    job = add_job(factory, user)
    run_id = new_run(factory, user, {}, job_id=job, agent="triage")
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), run_id) is RunStatus.done
    assert backends.made[0].seen_prompt == f"{message}\n\nJob id: {job}"
    assert run_row(factory, run_id).agent_version == 1


def test_a_saved_wall_time_and_model_per_credential_reach_the_run(
    api: Api,
    admin: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = definitions.ECHO
    lowered = code.wall_time_s / 4
    changes = {
        "wall_time_s": lowered,
        "model_by_credential": {"anthropic_api_key": "claude-sonnet-5-5"},
    }
    assert not code.model_by_credential
    assert code.model == "claude-opus-5-5"
    assert save(api, admin, "echo", changes).status_code == 201
    timeouts: list[float | None] = []
    wait = asyncio.wait

    def watched(tasks: Any, *, timeout: float | None = None, **kwargs: Any) -> Any:
        if kwargs.get("return_when") == asyncio.FIRST_COMPLETED:
            timeouts.append(timeout)  # the runner's clock on the stream (runner._run_stream)
        return wait(tasks, timeout=timeout, **kwargs)

    monkeypatch.setattr(runner.asyncio, "wait", watched)
    on_key, options, _ = _run_echo(settings, factory, keyring, monkeypatch)
    assert timeouts == [lowered]
    assert options.model == "claude-sonnet-5-5"
    assert run_row(factory, on_key).model == "claude-sonnet-5-5"
    assert run_row(factory, on_key).agent_version == 1

    # A credential kind the version does not name runs on the default model.
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user, OAUTH, CredentialKind.claude_subscription)
    on_plan = new_run(factory, user)
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), on_plan) is RunStatus.done
    assert backends.made[0].seen_options.model == "claude-opus-5-5"
    assert run_row(factory, on_plan).model == "claude-opus-5-5"
    assert timeouts == [lowered, lowered]


def test_the_page_and_the_list_say_when_the_code_changed_a_saved_field(
    api: Api, admin: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = definitions.TRIAGE
    saved = save(api, admin, "triage", {"system_prompt": "Be brief.", "max_turns": 5})
    assert saved.status_code == 201
    assert saved.json()["code_changed"] == []
    assert saved.json()["code_prompt_then"] is None
    [row] = versions(factory, "triage")
    assert row.code_hashes == {
        "max_turns": overrides.code_hash(code.max_turns),
        "system_prompt": overrides.code_hash(code.system_prompt),
    }
    assert row.code_prompt == code.system_prompt

    def listed() -> list[str]:
        [item] = [a for a in api.get(AGENTS, token=admin).json()["items"] if a["name"] == "triage"]
        return list(item["code_changed"])

    assert listed() == []
    # The code's prompt changes after the version was saved (a new release).
    newer = dataclasses.replace(code, system_prompt=code.system_prompt + "\nA new rule.")
    monkeypatch.setitem(definitions.AGENTS, "triage", newer)
    page = api.get(f"{AGENTS}/triage", token=admin).json()
    assert page["code_changed"] == ["system_prompt"]
    assert page["code_prompt_then"] == code.system_prompt
    assert page["code"]["system_prompt"] == newer.system_prompt
    assert page["effective"]["system_prompt"] == "Be brief."  # the run still uses the saved one
    assert listed() == ["system_prompt"]
    monkeypatch.setitem(definitions.AGENTS, "triage", dataclasses.replace(newer, max_turns=20))
    page = api.get(f"{AGENTS}/triage", token=admin).json()
    assert page["code_changed"] == ["system_prompt", "max_turns"]
    # Saved again against the new code: nothing to report until the code changes again.
    again = save(api, admin, "triage", {"system_prompt": "Be brief."}, base_version=1)
    assert again.status_code == 201
    assert again.json()["code_changed"] == []
    assert again.json()["code_prompt_then"] is None
    assert listed() == []
    # A version that changes nothing has nothing the code could change under it.
    monkeypatch.setitem(definitions.AGENTS, "triage", code)
    assert listed() == ["system_prompt"]
    reverted = save(api, admin, "triage", {}, base_version=2)
    assert reverted.json()["code_changed"] == []
    assert listed() == []


# --- the next run uses it ----------------------------------------------------------------------


def test_a_wall_time_may_be_raised_up_to_the_ceiling_and_never_above(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    ceiling = overrides.ceiling_s()
    # Today the longest agent of the app is the website search (B20, a day).
    assert ceiling == max(agent.wall_time_s for agent in catalog.load().values()) == 86_400
    above = save(api, admin, "triage", {"wall_time_s": ceiling + 1})
    assert above.status_code == 422
    assert above.json()["detail"] == (
        "wall_time_s: at most 86400 seconds. It runs as its own job on the agents queue, which "
        "lets a run take this long."
    )
    for raw in ("Infinity", "NaN", "1e400"):
        answer = api.post(
            f"{AGENTS}/triage/versions",
            token=admin,
            content=f'{{"changes": {{"wall_time_s": {raw}}}}}',
            headers={"Content-Type": "application/json"},
        )
        assert answer.status_code == 422, (raw, answer.text)
    assert versions(factory, "triage") == []

    raised = save(api, admin, "triage", {"wall_time_s": ceiling})
    assert raised.status_code == 201, raised.text
    body = raised.json()
    assert body["saved"] == {"wall_time_s": ceiling}
    assert body["effective"]["wall_time_s"] == ceiling > definitions.TRIAGE.wall_time_s
    assert body["code"]["wall_time_s"] == definitions.TRIAGE.wall_time_s
    assert body["choices"]["wall_time_max_s"] == ceiling  # the page shows it by the field
    assert body["choices"]["wall_time_where"] == catalog.ON_THE_QUEUE
    assert "It has run for 24 hours (wall_time_s): the runner stops it." in body["ends"]
    [triage] = [
        a for a in api.get("/api/agents", token=admin).json()["items"] if a["name"] == "triage"
    ]
    assert triage["wall_time_s"] == ceiling


def _watch_the_runs_clock(monkeypatch: pytest.MonkeyPatch, at_once: bool = False) -> list[Any]:
    """The timeouts the runner's wait on the stream gets (runner._run_stream). `at_once`: that
    clock then runs out at once, so the run ends on its time limit."""
    timeouts: list[float | None] = []
    wait = asyncio.wait

    def watched(tasks: Any, *, timeout: float | None = None, **kwargs: Any) -> Any:
        if kwargs.get("return_when") == asyncio.FIRST_COMPLETED:
            timeouts.append(timeout)
            if at_once:
                timeout = 0
        return wait(tasks, timeout=timeout, **kwargs)

    monkeypatch.setattr(runner.asyncio, "wait", watched)
    return timeouts


def test_a_raised_wall_time_reaches_the_runs_clock_and_its_time_limit(
    api: Api,
    admin: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised = overrides.ceiling_s()
    assert raised > definitions.ECHO.wall_time_s
    assert save(api, admin, "echo", {"wall_time_s": raised}).status_code == 201
    timeouts = _watch_the_runs_clock(monkeypatch, at_once=True)
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user)
    run_id = new_run(factory, user, {"message": "x", "script": "stall"})
    assert execute(context(settings, factory, keyring, Backends()), run_id) is RunStatus.failed
    assert timeouts == [raised]
    row = run_row(factory, run_id)
    assert row.agent_version == 1
    # What the run says of its limit is the raised one, not the code's 10 minutes.
    assert row.error == "The run took longer than its limit of 1440 minutes and was stopped."
    # Run by worker.run_agent, the job's bound is the most it gets (a version saved since it
    # was read cannot outlive the job).
    capped = new_run(factory, user, {"message": "x", "script": "stall"})
    rc = context(settings, factory, keyring, Backends())
    assert asyncio.run(runner.execute(rc, capped, wall_time_max_s=1200.0)) is RunStatus.failed
    assert timeouts == [raised, 1200.0]
    assert run_row(factory, capped).error == (
        "The run took longer than its limit of 20 minutes and was stopped."
    )


def test_a_version_saved_above_a_lowered_ceiling_runs_at_the_ceiling(
    api: Api,
    admin: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ceiling = overrides.ceiling_s()
    assert save(api, admin, "echo", {"wall_time_s": ceiling}).status_code == 201
    # The code then shortens its longest agent: the ceiling comes down with it, and so do the
    # worker's job timeout and stale bound (worker.longest_agent_s).
    longest = max(catalog.load().values(), key=lambda agent: agent.wall_time_s)
    shorter = dataclasses.replace(longest, wall_time_s=definitions.ECHO.wall_time_s)
    monkeypatch.setitem(definitions.AGENTS, longest.name, shorter)
    lowered = overrides.ceiling_s()
    assert definitions.ECHO.wall_time_s < lowered < ceiling
    timeouts = _watch_the_runs_clock(monkeypatch)
    _run_echo(settings, factory, keyring, monkeypatch)
    assert timeouts == [lowered]
    page = api.get(f"{AGENTS}/echo", token=admin).json()
    assert page["saved"] == {"wall_time_s": ceiling}
    assert page["effective"]["wall_time_s"] == page["choices"]["wall_time_max_s"] == lowered


def fresh(script: str) -> Any:
    """What `script` prints last, as JSON, in a fresh interpreter of the api."""
    ran = subprocess.run(
        [sys.executable, "-c", "import json\n" + script],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    return json.loads(ran.stdout.strip().splitlines()[-1])


def test_an_agent_inside_another_job_is_refused_above_that_jobs_bound(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    """Review of A7: the ceiling is the agent's own. apply_check runs inside the decision of a
    Full auto item (apply_decide), so it gets that job's bound, not the agents queue's day."""
    check = catalog.load()["apply_check"]
    ceiling = catalog.ceiling_of(check)
    assert ceiling.seconds == overrides.ceiling_s(check) < overrides.ceiling_s()
    above = save(api, admin, "apply_check", {"wall_time_s": ceiling.seconds + 1})
    assert above.status_code == 422
    assert above.json()["detail"] == (
        f"wall_time_s: at most {ceiling.seconds:g} seconds. It runs inside the decision of a "
        "Full auto item (apply_decide), whose job ends there."
    )
    assert versions(factory, "apply_check") == []
    raised = save(api, admin, "apply_check", {"wall_time_s": ceiling.seconds})
    assert raised.status_code == 201, raised.text
    body = raised.json()
    assert body["effective"]["wall_time_s"] == ceiling.seconds > check.wall_time_s
    assert body["choices"]["wall_time_max_s"] == ceiling.seconds
    assert body["choices"]["wall_time_where"] == ceiling.where
    # A version saved under a higher ceiling runs at this one.
    assert overrides.apply(check, {"wall_time_s": 86_400.0}).wall_time_s == ceiling.seconds


def test_every_agent_has_the_ceiling_of_the_job_that_runs_it() -> None:
    """Review of A7, every agent of the app (catalog.load) has a ceiling, the bound of the job
    that actually runs it, compared with the queues a fresh worker builds: its own run_agent
    job's (arq's outer timeout less the margin), or that of the other module's job it runs
    inside. A new agent must be placed here."""
    from app.apply import form_agent
    from app.browser import leases
    from app.ingestion import engine
    from app.jobs import sweep

    found = fresh(
        "from app import worker\n"
        "specs = {s.name: {f.name: f.timeout_s for f in s.functions} for s in worker.queues()}\n"
        "from app.agents import catalog, definitions, overrides\n"
        "at_start = sorted(definitions.AGENTS)\n"
        "ceilings = {name: [overrides.ceiling_s(one), catalog.ceiling_of(one).where]\n"
        "    for name, one in catalog.load().items()}\n"
        "print(json.dumps({'specs': specs, 'at_start': at_start, 'ceilings': ceilings}))\n"
    )
    agents, apply, sweeps = (
        found["specs"][name] for name in (queues.AGENTS, queues.APPLY, queues.SWEEPS)
    )
    margin = worker.AGENT_JOB_MARGIN_S
    ceilings = {name: seconds for name, (seconds, _) in found["ceilings"].items()}
    # The decision's bound is the agents queue's job timeout, built from the agents a worker
    # registers when it starts: every agent but a sweep's.
    assert sorted(set(ceilings) - set(found["at_start"])) == sorted(catalog.SWEEP_AGENTS)
    from app.apply import applier

    inside = {
        "apply_agent": applier.APPLY_AGENT.wall_time_s,  # One click Apply's fill (A19)
        "apply_check": agents["apply_decide"] - margin,
        "apply_form": form_agent.APPLY_FORM.wall_time_s,  # the fill's form deadline
        "apply_answer": leases.LEASE_MAX_S["apply_fill"],  # the fill's browser lease
        "browser_finder": sweep.BROWSER_SEARCH_MAX_S,  # the sweep's own bound
        "browser_discovery": sweep.BROWSER_SEARCH_MAX_S,
        "profile_extract": engine.RUN_TIME.total_seconds(),  # the ingestion run's deadline
        "document_transcribe": engine.RUN_TIME.total_seconds(),
    }
    expected = {name: inside.get(name, agents["run_agent"] - margin) for name in ceilings}
    assert ceilings == expected
    assert agents["run_agent"] - margin == 86_400
    assert agents["apply_decide"] - margin == 3600
    # The other jobs end after their agents' ceilings.
    assert leases.LEASE_MAX_S["sweep"] >= sweep.BROWSER_SEARCH_MAX_S < sweeps["run_sweep"]
    assert max(inside["apply_form"], inside["apply_answer"]) < apply["apply_fill"]
    assert inside["profile_extract"] <= agents["apply_decide"] - margin  # its own job's bound
    # Every agent not inside another job is one the app starts as its own run.
    own = sorted(name for name in ceilings if name not in inside)
    # profile_titles and profile_interpret are each their own job on the agents queue, with the
    # queue's timeout.
    assert all(
        name in ("echo", "job_chat", "profile_interpret", "profile_titles", "triage")
        or name.startswith("documents_")
        for name in own
    )
    for name, (_, where) in found["ceilings"].items():
        assert where.startswith("It runs "), name
        assert where.endswith("."), name
        assert [dash for dash in (" - ", "\u2013", "\u2014") if dash in where] == [], name


def test_a_fresh_worker_keeps_the_queues_timeout_and_lets_a_raised_run_take_the_ceiling() -> None:
    """The agents queue a fresh worker builds keeps its job timeout of before A7 (the longest
    agent it registers, plus the margin), and arq's own timeout of run_agent, the outer bound
    of a raised run (worker.run_agent ends each job at its own bound first), covers the ceiling
    a fresh api computes, in another interpreter that never imports the worker."""
    built = fresh(
        "from app import worker\n"
        "spec = worker.agents_queue()\n"
        "timeouts = {one.name: one.timeout_s for one in spec.functions}\n"
        "print(json.dumps({'queue': spec.job_timeout_s, 'run_agent': timeouts['run_agent'],\n"
        "    'apply_decide': timeouts['apply_decide'], 'longest': worker.longest_agent_s(),\n"
        "    'outer': worker.RUN_AGENT_MAX_S, 'margin': worker.AGENT_JOB_MARGIN_S}))\n"
    )
    api = fresh(
        "from app.agents import overrides\nprint(json.dumps({'ceiling': overrides.ceiling_s()}))\n"
    )
    assert built["queue"] == built["apply_decide"] == built["longest"] + built["margin"] == 3720
    assert built["run_agent"] == built["outer"] == api["ceiling"] + built["margin"] == 86_520
    # The bound of one job: the queue's, unless the run's wall time was raised past it.
    assert worker.run_job_timeout_s(None, 3720) == 3720
    assert worker.run_job_timeout_s(definitions.TRIAGE.wall_time_s, 3720) == 3720
    assert worker.run_job_timeout_s(7200, 3720) == 7320
    assert worker.run_job_timeout_s(api["ceiling"], 3720) == built["outer"]
    assert worker.run_job_timeout_s(10.0**9, 3720) == built["outer"]  # never above arq's
    # Never below the queue's own job timeout either, were it above the outer bound.
    above = built["outer"] + 1000
    assert worker.run_job_timeout_s(None, above) == above
    assert worker.run_job_timeout_s(10.0**9, above) == above
    assert worker.run_job_timeout_s(60.0, above) == above


def test_a_raised_run_gets_its_own_job_bound_and_another_keeps_the_queues(
    api: Api,
    admin: str,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised = overrides.ceiling_s()
    assert save(api, admin, "echo", {"wall_time_s": raised}).status_code == 201
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    echo_run = new_run(factory, user)
    triage_run = new_run(factory, user, agent="triage")
    gone_run = new_run(factory, user, agent="g7_no_such_agent")
    assert overrides.run_wall_time_s(factory, echo_run) == raised
    assert overrides.run_wall_time_s(factory, triage_run) == definitions.TRIAGE.wall_time_s
    assert overrides.run_wall_time_s(factory, gone_run) is None
    assert overrides.run_wall_time_s(factory, uuid.uuid4()) is None

    bounds: list[float] = []
    capped: list[float | None] = []
    timeout = asyncio.timeout

    def recorded(delay: float | None) -> Any:
        bounds.append(typing.cast(float, delay))
        return timeout(delay)

    async def fake_execute(
        context: Any, run_id: uuid.UUID, *, wall_time_max_s: float | None = None
    ) -> RunStatus:
        capped.append(wall_time_max_s)
        return RunStatus.done

    monkeypatch.setattr(worker.asyncio, "timeout", recorded)
    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(worker, "agent_context", lambda ctx: None)
    ctx = {"sessions": factory}
    for run_id in (echo_run, triage_run, gone_run):
        assert asyncio.run(worker.run_agent(ctx, str(run_id), default_timeout_s=3720)) == "done"
    assert bounds == [raised + worker.AGENT_JOB_MARGIN_S, 3720, 3720]
    assert capped == [raised, 3720 - worker.AGENT_JOB_MARGIN_S, 3720 - worker.AGENT_JOB_MARGIN_S]


def test_the_reaper_waits_for_a_raised_runs_own_bound_and_reaps_a_normal_run_at_the_queues(
    api: Api, admin: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = overrides.ceiling_s()
    assert save(api, admin, "echo", {"wall_time_s": raised}).status_code == 201
    assert save(api, admin, "echo", {"effort": "high"}, base_version=1).status_code == 201
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    queue_bound = timedelta(seconds=3900)
    started = now() - queue_bound - timedelta(seconds=100)
    on_raised, on_code, on_other = (new_run(factory, user) for _ in range(3))
    for run_id, version in ((on_raised, 1), (on_code, None), (on_other, 2)):
        with factory() as db, db.begin():
            db.execute(
                sa.update(AgentRun)
                .where(AgentRun.id == run_id)
                .values(status=RunStatus.running, started_at=started, agent_version=version)
            )
    monkeypatch.setattr(worker, "agent_stale_running", lambda: queue_bound)
    # Past the queue's bound: the runs of the code's wall time and of a version that does not
    # change it are failed; the raised one is not, its own clock has a day to go.
    assert worker._reap_agent_runs(factory, now(), at_start=False) == 2
    assert run_row(factory, on_code).status is RunStatus.failed
    assert run_row(factory, on_other).status is RunStatus.failed
    assert run_row(factory, on_raised).status is RunStatus.running
    own = worker.own_stale_after(raised)
    assert own == timedelta(seconds=raised + worker.AGENT_STALE_MARGIN_S)
    assert worker._reap_agent_runs(factory, started + own, at_start=False) == 0  # at the limit
    later = started + own + timedelta(seconds=1)
    assert worker._reap_agent_runs(factory, later, at_start=False) == 1
    assert run_row(factory, on_raised).status is RunStatus.failed


def test_the_recent_runs_name_the_person_of_each_run_to_an_admin_only(
    api: Api, admin: str, member: str, factory: sessionmaker[Session]
) -> None:
    with factory() as db:
        ada = db.scalars(sa.select(User.id).where(User.email == "e2e-admin@example.org")).one()
    other = add_user(factory, "e2e-other@example.org")
    nameless = make_user(factory, email="e2e-nameless@example.org", name="", role=Role.member).id
    own = new_run(factory, ada)
    theirs = new_run(factory, other)
    blank = new_run(factory, nameless)
    started = now()
    with factory() as db, db.begin():
        db.execute(sa.update(AgentRun).where(AgentRun.id == theirs).values(started_at=started))
    page = api.get(f"{AGENTS}/echo", token=admin)
    assert page.status_code == 200
    shown = {row["id"]: row for row in page.json()["runs"]}
    assert (shown[str(own)]["user_name"], shown[str(own)]["user_email"]) == (
        "Ada",
        "e2e-admin@example.org",
    )
    assert shown[str(own)]["mine"] is True
    assert shown[str(own)]["started_at"] is None  # queued, not started yet
    assert (shown[str(theirs)]["user_name"], shown[str(theirs)]["user_email"]) == (
        "e2e-other",
        "e2e-other@example.org",
    )
    assert shown[str(theirs)]["mine"] is False  # their run: no link to its page
    assert shown[str(theirs)]["started_at"] is not None
    # A person without a name: the page shows the e-mail in its place, as the Users page does.
    assert (shown[str(blank)]["user_name"], shown[str(blank)]["user_email"]) == (
        "",
        "e2e-nameless@example.org",
    )
    # A member never reads it.
    refused = api.get(f"{AGENTS}/echo", token=member)
    assert refused.status_code == 404
    assert "e2e-other@example.org" not in refused.text


def _run_echo(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[uuid.UUID, Any, list[tuple[str, ...]]]:
    user = add_user(factory, f"e2e-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user)
    run_id = new_run(factory, user)
    served: list[tuple[str, ...]] = []
    build = runner.app_tools.build

    def recording(*args: Any, **kwargs: Any) -> Any:
        served.append(tuple(args[2]))
        return build(*args, **kwargs)

    monkeypatch.setattr(runner.app_tools, "build", recording)
    backends = Backends()
    assert execute(context(settings, factory, keyring, backends), run_id) is RunStatus.done
    return run_id, backends.made[0].seen_options, served


def test_the_next_run_uses_the_saved_version_and_records_it(
    api: Api,
    admin: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before, options, served = _run_echo(settings, factory, keyring, monkeypatch)
    assert options.system_prompt == definitions.ECHO.system_prompt
    assert run_row(factory, before).agent_version is None
    changes = {
        "system_prompt": "Answer in one word.",
        "max_turns": 2,
        "effort": "medium",
        "model": "claude-sonnet-5-5",
        "tools": [],
    }
    assert save(api, admin, "echo", changes).status_code == 201
    run_id, options, served = _run_echo(settings, factory, keyring, monkeypatch)
    assert options.system_prompt == "Answer in one word."
    assert options.max_turns == 2
    assert options.effort == "medium"
    assert options.model == "claude-sonnet-5-5"
    # Switched off: not allowed, and not served by the run's MCP server either.
    assert options.allowed_tools == []
    assert served == [()]
    assert run_row(factory, run_id).agent_version == 1
    assert run_row(factory, run_id).model == "claude-sonnet-5-5"

    # The page shows the run's numbers and version, and the options the runner built.
    page = api.get(f"{AGENTS}/echo", token=admin).json()
    shown = page["claude_code"]
    for key in ("tools", "disallowed_tools", "permission_mode", "setting_sources", "max_turns"):
        assert shown[key] == getattr(options, key), key
    assert shown["allowed_tools"] == options.allowed_tools
    assert shown["strict_mcp_config"] is options.strict_mcp_config is True
    assert shown["effort"] == options.effort
    assert shown["model"] == options.model
    assert shown["thinking"] == options.thinking
    assert [tool["on"] for tool in page["tools"]] == [False]
    newest = page["runs"][0]
    assert newest["id"] == str(run_id)
    assert newest["version"] == 1
    assert newest["turns"] == 1
    assert newest["mine"] is False  # another user's run: no link to its page

    # Revert: a new version with no change, and the next run is the code's again.
    reverted = save(api, admin, "echo", {}, base_version=1, note="Back to the code")
    assert reverted.status_code == 201
    assert reverted.json()["saved"] == {}
    run_id, options, served = _run_echo(settings, factory, keyring, monkeypatch)
    assert options.system_prompt == definitions.ECHO.system_prompt
    assert options.allowed_tools == ["mcp__app__get_job"]
    assert served == [("get_job",)]
    assert run_row(factory, run_id).agent_version == 2
    assert [row.version for row in versions(factory, "echo")] == [1, 2]


def test_the_runs_list_shows_the_saved_values(api: Api, admin: str) -> None:
    assert (
        save(api, admin, "triage", {"description": "Reads a posting.", "max_turns": 5}).status_code
        == 201
    )
    [triage] = [
        a for a in api.get("/api/agents", token=admin).json()["items"] if a["name"] == "triage"
    ]
    assert triage["description"] == "Reads a posting."
    assert triage["max_turns"] == 5
    summary = [a for a in api.get(AGENTS, token=admin).json()["items"] if a["name"] == "triage"]
    assert summary[0]["version"] == 1
    assert summary[0]["overridden"] is True


def test_a_failed_read_runs_the_code_definition_and_warns_by_type(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = secrets.token_hex(8)
    with factory() as db, db.begin():
        db.add(
            AgentOverride(
                agent="echo", version=1, changes={"system_prompt": "Saved."}, saved_at=now()
            )
        )

    def broken(db: Session, agent: str) -> None:
        raise sa.exc.OperationalError(marker, {}, Exception(marker))

    monkeypatch.setattr(overrides, "latest", broken)
    caplog.set_level(logging.WARNING, logger="app.agents.overrides")
    run_id, options, _ = _run_echo(settings, factory, keyring, monkeypatch)
    assert options.system_prompt == definitions.ECHO.system_prompt
    assert run_row(factory, run_id).agent_version is None
    [record] = [r for r in caplog.records if r.name == "app.agents.overrides"]
    assert record.error == "OperationalError"  # type: ignore[attr-defined]
    assert marker not in record.getMessage()
    assert not record.exc_info


def test_a_version_saved_against_older_code_keeps_what_still_fits() -> None:
    changes = {"tools": ["get_job", "gone_tool"], "wall_time_s": 10.0**9, "effort": "high"}
    used = overrides.apply(definitions.TRIAGE, changes)
    assert used.tools == ("get_job",)
    assert used.wall_time_s == overrides.ceiling_s()  # never above the ceiling of now
    assert used.effort == "high"
    assert used.private_io is definitions.TRIAGE.private_io
    assert overrides.apply(definitions.TRIAGE, {"model": "not-a-model"}) is definitions.TRIAGE


# --- what the page never shows ------------------------------------------------------------------


def test_a_private_agent_page_shows_numbers_only(
    api: Api, admin: str, factory: sessionmaker[Session]
) -> None:
    catalog.load()
    assert definitions.AGENTS["profile_extract"].private_io
    canary = f"canary-{secrets.token_hex(8)}"
    user = add_user(factory, "e2e-owner@example.org")
    with factory() as db, db.begin():
        run = AgentRun(
            user_id=user,
            agent="profile_extract",
            status=RunStatus.failed,
            input={"document": canary},
            output={"turns": 3, "tool_calls": 2, "note": canary},
            error=canary,
            model="claude-opus-5-5",
            input_tokens=10,
            output_tokens=5,
        )
        db.add(run)
        db.flush()
        db.add(AgentEvent(run_id=run.id, seq=1, kind="text", data={"text": canary}))
    page = api.get(f"{AGENTS}/profile_extract", token=admin)
    assert page.status_code == 200
    assert canary not in page.text
    [shown] = page.json()["runs"]
    assert shown["turns"] == 3
    assert shown["input_tokens"] == 10
    assert shown["status"] == "failed"
    assert set(shown) == {
        "id", "status", "model", "version", "turns", "input_tokens", "output_tokens",
        "created_at", "started_at", "finished_at", "user_name", "user_email", "mine",
    }  # fmt: skip
    # The person it ran for is shown (A7), and nothing of what it read or wrote.
    assert (shown["user_name"], shown["user_email"]) == ("e2e-owner", "e2e-owner@example.org")
    assert canary not in api.get(AGENTS, token=admin).text


def test_the_page_names_environment_variables_never_their_values(
    api: Api, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = f"sk-ant-api03-{secrets.token_hex(12)}"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("PATH", f"/opt/{secret}")
    page = api.get(f"{AGENTS}/triage", token=admin)
    assert "ANTHROPIC_API_KEY" in page.json()["environment"]
    assert secret not in page.text


# --- the tools as the worker describes them -------------------------------------------------------


def test_the_worker_publishes_the_tools_and_the_page_shows_them(
    api: Api, admin: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = api.get(f"{AGENTS}/triage", token=admin).json()["tools"]
    assert all(tool["description"] is None for tool in before)  # nothing published yet
    monkeypatch.setattr(catalog, "_published", frozenset())
    catalog.publish_once(factory, now())
    tools = api.get(f"{AGENTS}/triage", token=admin).json()["tools"]
    [status] = [tool for tool in tools if tool["name"] == "propose_status"]
    assert "Propose a new status" in status["description"]
    assert status["input_schema"]["required"] == ["status", "reason"]
    assert status["module"] == "app.agents.tools"
    assert status["full_name"] == "mcp__app__propose_status"
    # Once published, the next tick writes nothing.
    with factory() as db, db.begin():
        db.execute(sa.text("DELETE FROM agent_tools"))
    catalog.publish_once(factory, now())
    with factory() as db:
        assert db.scalar(sa.text("SELECT count(*) FROM agent_tools")) == 0


def test_a_failed_publish_is_tried_again_at_the_next_tick(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog, "_published", frozenset())

    def broken() -> Session:
        raise sa.exc.OperationalError("x", {}, Exception("x"))

    catalog.publish_once(broken, now())  # type: ignore[arg-type]  # never raises
    assert catalog._published == frozenset()
    catalog.publish_once(factory, now())
    assert "get_job" in catalog._published


# --- migration 0031 ------------------------------------------------------------------------------


def test_migration_0031_goes_down_and_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0030")
    with at_head.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        columns = {c["name"] for c in sa.inspect(conn).get_columns("agent_runs")}
    assert not {"agent_overrides", "agent_tools"} & tables
    assert "agent_version" not in columns
    command.upgrade(alembic_config(), "head")
    with at_head.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        columns = {c["name"] for c in sa.inspect(conn).get_columns("agent_runs")}
    assert {"agent_overrides", "agent_tools"} <= tables
    assert "agent_version" in columns
