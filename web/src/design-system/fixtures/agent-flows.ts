import {
  agentFlowSchema,
  type AgentFlow,
} from "@/features/admin/api/agents-api";

import fixture from "./agent-flows.json";

/**
 * The workflow of each category, as GET /api/admin/agents serves it (catalog.FLOWS, agents A9).
 * agent-flows.json is written by `uv run --directory api python -m tests.agents.regen_agent_flows`
 * and api/tests/agents/test_admin_agents.py fails when it drifts from the api.
 */
export const AGENT_FLOWS = Object.fromEntries(
  Object.entries(fixture.flows).map(([key, flow]) => [
    key,
    agentFlowSchema.parse(flow),
  ]),
) as Record<keyof typeof fixture.flows, AgentFlow>;

/** Every agent of the app, as the code registers it. */
export const REGISTERED_AGENTS = fixture.agents;
