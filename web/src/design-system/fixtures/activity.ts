import { delay, http, HttpResponse } from "msw";

import type { AgentFlow } from "@/features/admin/api/agents-api";
import {
  ACTIVE_FLOW_STATUSES,
  type FlowStatus,
  type FlowStep,
  type Workflow,
  type WorkflowRun,
  type WorkflowRunDetail,
} from "@/features/agents/api/activity-api";
import type { Run, RunStep } from "@/features/agents/api/runs-api";
import { server } from "@/test/server";

import { AGENT_FLOWS } from "./agent-flows";
import { fakeRunsApi, makeRun } from "./runs";

/** Invented workflow runs for the Activity stories: believable, never a real company. */

/** Minutes before now, as the api writes a time: a running step's duration counts from it. */
const ago = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

/** A workflow's graph as the api serves it: the boxes of its category's flow it names, the
 * arrows between them, the empty columns and rows taken out. */
function subflow(category: keyof typeof AGENT_FLOWS, ids: string[]): AgentFlow {
  const flow: AgentFlow = AGENT_FLOWS[category];
  const nodes = flow.nodes.filter((node) => ids.includes(node.id));
  const columns = [...new Set(nodes.map((node) => node.column))].sort(
    (a, b) => a - b,
  );
  const rows = [...new Set(nodes.map((node) => node.row))].sort(
    (a, b) => a - b,
  );
  return {
    nodes: nodes.map((node) => ({
      ...node,
      column: columns.indexOf(node.column),
      row: rows.indexOf(node.row),
    })),
    edges: flow.edges.filter(
      (edge) => ids.includes(edge.from) && ids.includes(edge.to),
    ),
  };
}

const PANEL = [
  "documents_panel_screen",
  "documents_panel_manager",
  "documents_panel_reader_manager",
  "documents_panel_reader_rival",
  "documents_panel_reader_ceo",
  "documents_panel_reader_recruiter",
  "documents_panel_reader_teammate",
];

export const WORKFLOWS: Workflow[] = [
  {
    key: "job_search",
    title: "Job search",
    sentence:
      "A sweep: each job website searched by an agent, then each new posting read.",
    flow: subflow("finding", [
      "search",
      "onboarding",
      "schedule",
      "scope",
      "sweep",
      "browser_discovery",
      "posting_reader",
      "help",
      "postings",
    ]),
  },
  {
    key: "posting_documents",
    title: "Documents for a posting",
    sentence:
      "A CV and a letter written for one posting, each screened by the hiring panel.",
    flow: subflow("documents", [
      "fit",
      "click",
      "documents_cv",
      "documents_letter",
      "documents_base",
      "panel",
      ...PANEL,
      "documents_panel_merge",
    ]),
  },
  {
    key: "application",
    title: "Application",
    sentence:
      "An application filled in, its open questions answered and checked before it is sent.",
    flow: subflow("applying", [
      "apply",
      "like",
      "queued",
      "documents",
      "fill",
      "apply_agent",
      "apply_answer",
      "apply_form",
      "question",
      "answers",
      "approve",
      "decision",
      "apply_check",
    ]),
  },
  {
    key: "fill_profile",
    title: "Fill my profile",
    sentence:
      "The documents you upload read, their scanned pages transcribed, and what goes into your profile proposed.",
    flow: subflow("profile", [
      "read",
      "run",
      "document_transcribe",
      "profile_extract",
      "items",
      "accept",
    ]),
  },
  {
    key: "job_chat",
    title: "Job chat",
    sentence: "An answer in a job's chat.",
    flow: subflow("jobs", ["message", "job_chat", "thread"]),
  },
];

const POSTING = {
  id: "8b1f6d53-2e4c-4a9f-a7b2-3d0e9f8a7b62",
  words: "Analytics engineer at Kestrel Health",
};

export const RUN_UNDER_WAY_ID = "3f6a1c2e-9b4d-4e7a-8c15-6d2b0e9f1a73";
export const FAILED_RUN_ID = "71c9e4b2-5a3d-4f86-b0e1-2d8c7a6f5e94";
export const PRIVATE_RUN_ID = "c25d8e1f-4b7a-4c39-9e60-1f3a8b2d7c05";
export const SEARCH_RUN_ID = "a94b2f07-1e6c-4d58-b3a9-7c0e5d2f8b16";
const CHAT_RUN_ID = "e0b7c3a9-6f2d-4a15-8e94-3b1c5d7a2f68";

/** The reviewer at work in the run under way: its log opens on the run's page. */
export const REVIEWER_STEP_ID = "4d2e8a6c-1b9f-4e37-a5c0-8f6b3d1e9a27";

function agentStep(
  id: string,
  node: string,
  name: string,
  status: FlowStep["status"],
  started: number | null,
  seconds: number | null,
  extra: Partial<FlowStep> = {},
): FlowStep {
  return {
    id,
    kind: "agent",
    node,
    name,
    agent: node,
    status,
    started_at: started === null ? null : ago(started),
    finished_at:
      started === null || seconds === null
        ? null
        : new Date(
            Date.now() - started * 60_000 + seconds * 1000,
          ).toISOString(),
    duration_s: seconds,
    private: false,
    note: null,
    ...extra,
  };
}

const run = (
  id: string,
  workflow: Workflow,
  changes: Partial<WorkflowRun>,
): WorkflowRun => ({
  id,
  workflow: workflow.key,
  workflow_title: workflow.title,
  trigger: { kind: "unknown", words: "Not recorded for this run", node: null },
  subject: null,
  status: "success",
  created_at: ago(30),
  started_at: ago(30),
  finished_at: ago(28),
  duration_s: 120,
  agent_runs: 1,
  ...changes,
});

const workflowOf = (key: string): Workflow => {
  const found = WORKFLOWS.find((one) => one.key === key);
  if (!found) throw new Error(`No workflow ${key} in the fixture`);
  return found;
};
const SEARCH = workflowOf("job_search");
const DOCUMENTS = workflowOf("posting_documents");
const APPLICATION = workflowOf("application");
const FILL = workflowOf("fill_profile");
const CHAT = workflowOf("job_chat");

/** A sweep with one website searched by its agent, `stepId`, in the state `status`. */
export function searchRunOf(
  stepId: string,
  status: FlowStatus,
): WorkflowRunDetail {
  const done = !ACTIVE_FLOW_STATUSES.has(status);
  return {
    ...run(SEARCH_RUN_ID, SEARCH, {
      trigger: {
        kind: "recorded",
        words: "Your click on Save and start search",
        node: "search",
      },
      status,
      finished_at: done ? ago(28) : null,
      duration_s: done ? 120 : null,
    }),
    flow: SEARCH.flow,
    steps: [
      agentStep(
        stepId,
        "browser_discovery",
        "Website search",
        status,
        30,
        done ? 120 : null,
      ),
    ],
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    credential_kind: null,
  };
}

/** The documents of a Write click, under way: the CV written, its hiring panel reading it. */
export const RUN_UNDER_WAY: WorkflowRunDetail = {
  ...run(RUN_UNDER_WAY_ID, DOCUMENTS, {
    trigger: {
      kind: "recorded",
      words: "Your click on Write now",
      node: "click",
    },
    subject: { words: POSTING.words, job_id: POSTING.id },
    status: "running",
    created_at: ago(3),
    started_at: ago(3),
    finished_at: null,
    duration_s: null,
    agent_runs: 8,
  }),
  flow: DOCUMENTS.flow,
  steps: [
    agentStep("1b7e", "documents_cv", "CV writer", "success", 3, 74),
    {
      id: "panel",
      kind: "system",
      node: "panel",
      name: "Hiring panel",
      agent: null,
      status: "running",
      started_at: ago(1.7),
      finished_at: null,
      duration_s: null,
      private: false,
      note: "Round 1 of 2",
    },
    agentStep(
      "2c8f",
      "documents_panel_screen",
      "Hiring panel: automated screen",
      "success",
      1.7,
      21,
    ),
    agentStep(
      "3d9a",
      "documents_panel_manager",
      "Hiring panel: hiring manager",
      "success",
      1.7,
      33,
    ),
    agentStep(
      "4e0b",
      "documents_panel_reader_manager",
      "Hiring panel: reader, hiring manager",
      "success",
      1.6,
      41,
    ),
    agentStep(
      REVIEWER_STEP_ID,
      "documents_panel_reader_ceo",
      "Hiring panel: reader, the CEO",
      "running",
      0.9,
      null,
    ),
    agentStep(
      "6a2d",
      "documents_panel_reader_rival",
      "Hiring panel: reader, another applicant",
      "running",
      0.8,
      null,
    ),
    agentStep(
      "7b3e",
      "documents_panel_reader_recruiter",
      "Hiring panel: reader, the recruiter",
      "queued",
      null,
      null,
    ),
  ],
  input_tokens: 48210,
  output_tokens: 3904,
  cost_usd: 0.3121,
  credential_kind: "claude_subscription",
};

/** An application whose answer writer failed: the run stopped there. */
export const FAILED_RUN: WorkflowRunDetail = {
  ...run(FAILED_RUN_ID, APPLICATION, {
    trigger: { kind: "recorded", words: "Your click on Apply", node: "apply" },
    subject: { words: "Platform engineer at Lumen Freight", job_id: null },
    status: "failure",
    created_at: ago(95),
    started_at: ago(95),
    finished_at: ago(88),
    duration_s: 412,
    agent_runs: 1,
  }),
  flow: APPLICATION.flow,
  steps: [
    {
      id: "fill",
      kind: "system",
      node: "fill",
      name: "Fill job",
      agent: null,
      status: "failure",
      started_at: ago(95),
      finished_at: ago(88),
      duration_s: 412,
      private: false,
      note: "The form was left as it was: nothing was sent.",
    },
    agentStep("8c4f", "apply_answer", "Answer writer", "failure", 93, 288),
  ],
  input_tokens: 22140,
  output_tokens: 1210,
  cost_usd: 0.0934,
  credential_kind: "anthropic_api_key",
};

/** A Fill my profile run that read a CV: the extractor's log keeps names and sizes only. */
export const PRIVATE_RUN: WorkflowRunDetail = {
  ...run(PRIVATE_RUN_ID, FILL, {
    trigger: {
      kind: "recorded",
      words: "Your click on Start reading",
      node: "read",
    },
    subject: { words: "cv-2026.pdf", job_id: null },
    created_at: ago(240),
    started_at: ago(240),
    finished_at: ago(237),
    duration_s: 168,
    agent_runs: 1,
  }),
  flow: FILL.flow,
  steps: [
    {
      id: "ingestion",
      kind: "system",
      node: "run",
      name: "Ingestion run",
      agent: null,
      status: "success",
      started_at: ago(240),
      finished_at: ago(237),
      duration_s: 168,
      private: false,
      note: "1 document read, 14 items proposed",
    },
    agentStep(
      "9d5a",
      "profile_extract",
      "Profile extractor",
      "success",
      240,
      151,
      {
        private: true,
      },
    ),
  ],
  input_tokens: 31800,
  output_tokens: 2260,
  cost_usd: 0.1409,
  credential_kind: "claude_subscription",
};

/** A week of activity, newest first. */
export const WORKFLOW_RUNS: WorkflowRun[] = [
  RUN_UNDER_WAY,
  run(CHAT_RUN_ID, CHAT, {
    trigger: {
      kind: "recorded",
      words: "Your message in the chat",
      node: "message",
    },
    subject: { words: POSTING.words, job_id: POSTING.id },
    created_at: ago(42),
    started_at: ago(42),
    finished_at: ago(41.8),
    duration_s: 11.2,
  }),
  FAILED_RUN,
  run(SEARCH_RUN_ID, SEARCH, {
    trigger: {
      kind: "recorded",
      words: "The schedule",
      node: "schedule",
    },
    subject: {
      words: "Data engineer, analytics engineer in Lyon or remote",
      job_id: null,
    },
    created_at: ago(180),
    started_at: ago(180),
    finished_at: ago(164),
    duration_s: 954,
    agent_runs: 4,
  }),
  PRIVATE_RUN,
];

/** The log of a private step: tool names and sizes, never the content. */
export const PRIVATE_STEPS: RunStep[] = [
  {
    seq: 1,
    at: "2026-11-05T08:14:02Z",
    kind: "status",
    data: { status: "queued" },
  },
  {
    seq: 2,
    at: "2026-11-05T08:14:03Z",
    kind: "status",
    data: { status: "running" },
  },
  {
    seq: 3,
    at: "2026-11-05T08:14:09Z",
    kind: "tool_call",
    data: { name: "mcp__app__read_document", input_size: 64 },
  },
  {
    seq: 4,
    at: "2026-11-05T08:14:10Z",
    kind: "tool_result",
    data: { size: 18342, blocks: 0 },
  },
  {
    seq: 5,
    at: "2026-11-05T08:16:34Z",
    kind: "status",
    data: { status: "done" },
  },
];

/**
 * The fake /api/activity for a story: the workflows, the runs (one workflow's when asked), one
 * run's detail; and the agent step's log through the fake /api/agents.
 */
export function fakeActivityApi(
  options: {
    runs?: WorkflowRun[];
    detail?: WorkflowRunDetail;
    step?: { detail?: Run; steps?: RunStep[]; open?: boolean };
    loading?: boolean;
  } = {},
) {
  const runs = options.runs ?? WORKFLOW_RUNS;
  const details = [RUN_UNDER_WAY, FAILED_RUN, PRIVATE_RUN];
  server.use(
    http.get("*/api/activity/workflows", () =>
      HttpResponse.json({ items: WORKFLOWS }),
    ),
    http.get("*/api/activity/runs", async ({ request }) => {
      if (options.loading) await delay("infinite");
      const workflow = new URL(request.url).searchParams.get("workflow");
      return HttpResponse.json({
        items: workflow
          ? runs.filter((one) => one.workflow === workflow)
          : runs,
      });
    }),
    http.get("*/api/activity/runs/:id", ({ params }) => {
      const found =
        options.detail ??
        details.find(
          (one) =>
            one.id === params.id ||
            // An agent run's id finds its workflow run, as the api does.
            one.steps.some((step) => step.id === params.id),
        );
      return found
        ? HttpResponse.json(found)
        : HttpResponse.json({ detail: "Not found" }, { status: 404 });
    }),
  );
  fakeRunsApi({
    detail: options.step?.detail ?? makeRun(),
    steps: options.step?.steps,
    open: options.step?.open,
  });
}
