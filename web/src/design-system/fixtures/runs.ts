import { delay, http, HttpResponse, sse } from "msw";

import type { Agent, Run, RunStep } from "@/features/agents/api/runs-api";
import { server } from "@/test/server";

/** Invented agent runs for the Assistant activity stories: believable, never a real company. */

export const RUN_ID = "5d1c0a7e-3b2f-4c61-9e85-0a4b7c2d9f10";

/** Minutes before now, as the api writes a time: a running run's duration counts from it. */
const ago = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

export function makeRun(changes: Partial<Run> = {}): Run {
  return {
    id: RUN_ID,
    agent: "triage",
    job_id: null,
    status: "done",
    input: {},
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: 18240,
    output_tokens: 612,
    cost_usd: 0.0841,
    credential_kind: "claude_subscription",
    cancel_requested: false,
    created_at: "2026-11-05T08:14:02Z",
    started_at: "2026-11-05T08:14:03Z",
    finished_at: "2026-11-05T08:14:21Z",
    duration_s: 18.4,
    job: null,
    ...changes,
  };
}

const LUMEN = {
  id: "7a0e5c42-1d3b-4f8e-b6a1-2c9d8e7f6a51",
  company: "Lumen Freight",
  title: "Senior data engineer",
};
/** The posting the finished run checked: its link sits above the run panel. */
export const KESTREL = {
  id: "8b1f6d53-2e4c-4a9f-a7b2-3d0e9f8a7b62",
  company: "Kestrel Health",
  title: "Analytics engineer",
};

/** A week of activity: a search still running, letters and checks done, one run that failed. */
export const RUNS: Run[] = [
  makeRun({
    id: "1e2d3c4b-5a69-4788-9a0b-1c2d3e4f5a01",
    agent: "browser_discovery",
    status: "running",
    input_tokens: 9120,
    output_tokens: 233,
    cost_usd: null,
    created_at: ago(2),
    started_at: ago(2),
    finished_at: null,
    duration_s: null,
  }),
  makeRun({
    id: "1e2d3c4b-5a69-4788-9a0b-1c2d3e4f5a02",
    agent: "documents_letter",
    job_id: LUMEN.id,
    job: LUMEN,
    input_tokens: 24310,
    output_tokens: 1488,
    cost_usd: 0.1927,
    created_at: "2026-11-05T09:40:12Z",
    duration_s: 41.7,
  }),
  makeRun({
    id: "1e2d3c4b-5a69-4788-9a0b-1c2d3e4f5a03",
    agent: "triage",
    job_id: KESTREL.id,
    job: KESTREL,
    credential_kind: "anthropic_api_key",
    created_at: "2026-11-05T08:14:02Z",
  }),
  makeRun({
    id: "1e2d3c4b-5a69-4788-9a0b-1c2d3e4f5a04",
    agent: "apply_form",
    job_id: "9c2a7e64-3f5d-4b0a-b8c3-4e1f0a9b8c73",
    job: null,
    status: "failed",
    error: "max_turns",
    input_tokens: 61022,
    output_tokens: 2210,
    cost_usd: 0.4612,
    created_at: "2026-11-04T17:02:45Z",
    duration_s: 312.5,
  }),
  makeRun({
    id: "1e2d3c4b-5a69-4788-9a0b-1c2d3e4f5a05",
    agent: "profile_titles",
    input_tokens: 3104,
    output_tokens: 188,
    cost_usd: 0.0123,
    credential_kind: null,
    created_at: "2026-11-03T11:20:00Z",
    duration_s: 6.2,
  }),
];

/** The test agent the server offers outside production (GET /api/agents, test_only). */
export const TEST_AGENT: Agent = {
  name: "echo",
  description: "Test agent",
  model: "claude-opus-5-5",
  max_turns: 4,
  wall_time_s: 600,
  test_only: true,
};

const step = (
  seq: number,
  kind: string,
  data: Record<string, unknown> = {},
): RunStep => ({ seq, at: "2026-11-05T08:14:05Z", kind, data });

/** A posting check from start to end: what it read, what it answered, what it cost. */
export const FINISHED_STEPS: RunStep[] = [
  step(1, "status", { status: "queued" }),
  step(2, "status", { status: "running" }),
  step(3, "init", {
    model: "claude-opus-5-5",
    tools: ["mcp__app__get_job", "mcp__app__get_profile"],
  }),
  step(4, "thinking"),
  step(5, "tool_call", {
    name: "mcp__app__get_job",
    input: { job_id: KESTREL.id },
  }),
  step(6, "tool_result", {
    content: JSON.stringify({
      title: KESTREL.title,
      company: KESTREL.company,
      location: "Remote, France",
      skills: ["SQL", "dbt", "Looker"],
    }),
  }),
  step(7, "text", {
    text: "The posting fits well: SQL, dbt and Looker are all in your profile. Healthcare data standards are new to you, which the letter can address.",
  }),
  step(8, "usage", {
    input_tokens: 18240,
    output_tokens: 612,
    cost_usd: 0.0841,
    credential_kind: "claude_subscription",
  }),
  step(9, "status", { status: "done" }),
];

/** The first steps of a run that is still at work: no answer and no total yet. */
export const RUNNING_STEPS: RunStep[] = FINISHED_STEPS.slice(1, 6);

/**
 * The fake /api/agents for a story: the agents offered, the list of runs, one run's detail and
 * its stream, which ends after its steps unless `open` keeps it going.
 */
export function fakeRunsApi(
  options: {
    agents?: Agent[];
    runs?: Run[];
    detail?: Run;
    steps?: RunStep[];
    open?: boolean;
  } = {},
) {
  const detail = options.detail ?? makeRun();
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json({ items: options.agents ?? [] }),
    ),
    http.get("*/api/agents/runs", () =>
      HttpResponse.json({ items: options.runs ?? RUNS }),
    ),
    http.get("*/api/agents/runs/:id", () =>
      HttpResponse.json({ ...detail, events: [], events_truncated: false }),
    ),
    sse<{ step: RunStep; end: object }>(
      "*/api/agents/runs/:id/stream",
      ({ client }) => {
        for (const one of options.steps ?? FINISHED_STEPS)
          client.send({ event: "step", data: one });
        // An event with no data is never dispatched by the browser: end carries an empty object.
        if (!options.open) client.send({ event: "end", data: {} });
      },
    ),
  );
}

/** The list of runs answers `status`, or never answers. */
export function failRunsList(answer: "error" | "loading") {
  server.use(
    http.get("*/api/agents/runs", async () => {
      if (answer === "loading") await delay("infinite");
      return HttpResponse.json(
        { detail: "Something went wrong on the server." },
        { status: 500 },
      );
    }),
  );
}
