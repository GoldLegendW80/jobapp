"""The normal Apply worker binds the generic runtime, with no browser or database needed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import worker
from app.apply import form_agent
from app.apply.ats import base, generic
from tests.apply.conftest import AT
from tests.conftest import make_settings


def test_the_default_worker_services_bind_the_generic_form_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx: dict[str, Any] = {
        "settings": make_settings("postgresql+psycopg://e2e:e2e@localhost/e2e"),
        "redis": object(),
        "sessions": object(),
        "clock": lambda: AT,
    }
    contexts: list[dict[str, Any]] = []
    agent_context = object()

    def runtime_context(found: dict[str, Any]) -> object:
        contexts.append(found)
        return agent_context

    monkeypatch.setattr(worker, "agent_context", runtime_context)
    built = worker.apply_services(ctx)
    assert contexts == []  # constructing services starts no model run
    assert isinstance(built.form_agent, form_agent.RuntimeFormAgent)
    assert built.form_agent.script is None
    assert built.form_agent.context() is agent_context
    assert contexts == [ctx]
    assert built.sessions is ctx["sessions"]
    assert built.clock() == AT


def test_explicit_worker_services_remain_the_same_object() -> None:
    given = object()
    assert worker.apply_services({"apply_services": given}) is given


def test_default_fake_stream_names_real_native_fields_and_submit() -> None:
    root = Path(__file__).resolve().parents[3]
    snapshot = (root / "tests/ats_forms/a8/snapshots/workable-initial.txt").read_text()
    tree = base.parse_snapshot(snapshot)
    found = base.discover_fields(tree, generic.Generic().submit_names)
    fields = {item.ref: item for item in found.fields}
    stream = json.loads((root / "api/app/agents/fake_streams/apply_form.json").read_text())
    calls = [
        block
        for step in stream["steps"]
        for block in step.get("message", {}).get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    ]
    filled = [call["input"]["ref"] for call in calls if call["name"] == "mcp__app__fill_field"]
    assert all(ref in fields for ref in filled)
    assert {item.ref for item in found.fields if item.required} <= set(filled)
    assert [fields[ref].label for ref in filled] == [
        "First name",
        "Last name",
        "Email",
        "Phone",
        "Resume",
        "How did you hear about us?",
        "Summary",
    ]
    (designated,) = [
        call["input"]["ref"] for call in calls if call["name"] == "mcp__app__designate_submit"
    ]
    assert generic.check_designation(tree, designated) == ""
