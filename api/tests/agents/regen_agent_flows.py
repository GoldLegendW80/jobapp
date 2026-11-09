"""Rewrite web/src/design-system/fixtures/agent-flows.json, the workflows and the registered agents
the web's stories and tests draw, from what GET /api/admin/agents serves. Run after changing
catalog.FLOWS, catalog.NAMES or an agent's definition:

    uv run --directory api python -m tests.agents.regen_agent_flows

test_admin_agents.py fails while the file differs from the api."""

import json
from pathlib import Path
from typing import Any

from app.agents import admin_router, catalog, definitions

COMMAND = "uv run --directory api python -m tests.agents.regen_agent_flows"
FIXTURE = Path(__file__).resolve().parents[3] / "web/src/design-system/fixtures/agent-flows.json"
# The fields of the list's items (admin_router.AgentSummary) the web fixture keeps of each agent.
FIELDS = (
    "name",
    "category",
    "description",
    "module",
    "model",
    "internal",
    "private_io",
    "test_only",
)


def document() -> dict[str, Any]:
    agents = catalog.load()
    flows: dict[str, Any] = {}
    for key, flow in catalog.FLOWS.items():
        served = admin_router._flow(flow)
        assert served is not None
        flows[key] = served.model_dump(by_alias=True)
    registered = []
    for name in sorted(agents):
        module = definitions.module_of(name)
        # Agents a test registers in its own module are no agent of the app.
        if not module.startswith("app."):
            continue
        agent = agents[name]
        registered.append(
            {
                "name": name,
                "category": catalog.category_of(agent),
                "description": agent.description,
                "module": module,
                "model": agent.model,
                "internal": agent.internal,
                "private_io": agent.private_io,
                "test_only": agent.test_only,
            }
        )
    return {
        "_comment": f"Written by `{COMMAND}`: the workflows and the registered agents as GET "
        "/api/admin/agents serves them. api/tests/agents/test_admin_agents.py fails when it "
        "drifts from the api.",
        "flows": flows,
        "agents": registered,
    }


def main() -> None:
    FIXTURE.write_text(
        json.dumps(document(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
