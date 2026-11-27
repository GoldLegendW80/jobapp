/**
 * A search's run in Activity with its two search assistants, and what each said when it ended
 * (jobs J30, the summary's "What the assistants said"). Invented data; the web search's words
 * hold a link and markup the summary must show as plain text.
 */
import { http, HttpResponse } from "msw";

export const WORDS_SWEEP_ID = "5a1e0c7d-3b9f-4c21-8d6e-1f2a3b4c5d6e";
export const WEBSITE_RUN_ID = "6b2f1d8e-4ca0-4d32-9e7f-2a3b4c5d6e7f";
export const WEB_RUN_ID = "7c3a2e9f-5db1-4e43-af80-3b4c5d6e7f80";

export const WEBSITE_WORDS =
  "I searched Welcome to the Jungle for GenAI Engineer in Saint-Denis and read about 80 results.\nI left a freelance mission and two internships, which the app keeps with a badge now.";
export const WEB_WORDS =
  "See [this posting](https://jobs.example.org/42) and <b>more</b> on the board.";

const step = (id: string, agent: string, name: string, at: string) => ({
  id,
  kind: "agent",
  node: agent,
  name,
  agent,
  status: "success",
  started_at: at,
  finished_at: at,
  duration_s: 300,
  private: true,
  note: null,
});

export const sweepWorkflowRun = {
  id: WORDS_SWEEP_ID,
  workflow: "job_search",
  workflow_title: "Job search",
  trigger: { kind: "recorded", words: "Your click on Find jobs", node: null },
  subject: null,
  status: "success",
  created_at: "2026-11-26T08:30:00Z",
  started_at: "2026-11-26T08:30:00Z",
  finished_at: "2026-11-26T08:45:00Z",
  duration_s: 900,
  agent_runs: 2,
  flow: null,
  steps: [
    {
      ...step(`sweep:${WORDS_SWEEP_ID}`, "", "Sweep", "2026-11-26T08:30:00Z"),
      kind: "system",
      node: "sweep",
      agent: null,
    },
    step(
      WEBSITE_RUN_ID,
      "browser_discovery",
      "Website search",
      "2026-11-26T08:31:00Z",
    ),
    step(WEB_RUN_ID, "web_discovery", "Web search", "2026-11-26T08:38:00Z"),
  ],
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
  credential_kind: null,
  reason: null,
  new_postings: 2,
};

const run = (id: string, agent: string, text: string) => ({
  id,
  agent,
  job_id: null,
  status: "done",
  input: {},
  output: null,
  error: null,
  model: null,
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
  credential_kind: null,
  cancel_requested: false,
  created_at: "2026-11-26T08:31:00Z",
  started_at: "2026-11-26T08:31:00Z",
  finished_at: "2026-11-26T08:36:00Z",
  duration_s: 300,
  job: null,
  events: [
    {
      seq: 1,
      at: "2026-11-26T08:32:00Z",
      kind: "text",
      data: { text: "Searching." },
    },
    { seq: 2, at: "2026-11-26T08:35:00Z", kind: "text", data: { text } },
    {
      seq: 3,
      at: "2026-11-26T08:36:00Z",
      kind: "status",
      data: { status: "done" },
    },
  ],
  events_truncated: false,
});

/** The fakes of the search's run and its two assistants' runs. */
export const assistantWordsHandlers = [
  http.get(`*/api/activity/runs/${WORDS_SWEEP_ID}`, () =>
    HttpResponse.json(sweepWorkflowRun),
  ),
  http.get(`*/api/agents/runs/${WEBSITE_RUN_ID}`, () =>
    HttpResponse.json(run(WEBSITE_RUN_ID, "browser_discovery", WEBSITE_WORDS)),
  ),
  http.get(`*/api/agents/runs/${WEB_RUN_ID}`, () =>
    HttpResponse.json(run(WEB_RUN_ID, "web_discovery", WEB_WORDS)),
  ),
];
