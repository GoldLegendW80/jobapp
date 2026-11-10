/**
 * Calls of /api/activity (SPEC-agents.md, Activity): the person's workflow runs in the pattern of
 * GitHub Actions. A workflow is one thing a person starts (Job search, Documents for a posting,
 * Application ...), with its graph; a workflow run groups the agent runs and the app's own steps
 * of one of them (a sweep, a click's documents, an application). An agent step's log is the
 * agent run's own, read and followed through runs-api.ts.
 */
import { z } from "zod";

import { agentFlowSchema } from "@/features/admin/api/agents-api";
import { request } from "@/lib/api";

import { credentialKinds } from "./runs-api";

/** A workflow run's or a step's status, in the words of the graph (waiting: still to come). */
export const flowStatuses = [
  "waiting",
  "queued",
  "running",
  "success",
  "failure",
  "skipped",
  "cancelled",
] as const;
export type FlowStatus = (typeof flowStatuses)[number];
const flowStatus = z.enum(flowStatuses).catch("skipped");

// While a workflow run is under way, its statuses and durations are read again this often.
export const ACTIVITY_POLL_MS = 2000;

// The Activity tab's count is read again this often, only while the document is visible.
export const ACTIVITY_COUNT_POLL_MS = 15_000;

export const ACTIVE_FLOW_STATUSES: ReadonlySet<FlowStatus> = new Set([
  "waiting",
  "queued",
  "running",
]);

export const workflowSchema = z.object({
  key: z.string().min(1),
  title: z.string().min(1),
  sentence: z.string(),
  /** None for the runs no workflow draws (a test agent, an agent the app does not start). */
  flow: agentFlowSchema.nullable(),
});
export type Workflow = z.infer<typeof workflowSchema>;

/** How a run was started, in words ("Your click on Write now"), and its box in the graph. */
export const triggerSchema = z.object({
  kind: z.enum(["recorded", "unknown"]),
  words: z.string(),
  node: z.string().nullable(),
});
export type Trigger = z.infer<typeof triggerSchema>;

/** What the run is about: a posting, a search, a document. */
export const subjectSchema = z.object({
  words: z.string(),
  job_id: z.string().nullable(),
});

export const workflowRunSchema = z.object({
  id: z.string().min(1),
  workflow: z.string(),
  workflow_title: z.string(),
  trigger: triggerSchema,
  subject: subjectSchema.nullable(),
  status: flowStatus,
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  duration_s: z.number().nullable(),
  agent_runs: z.number().int().nonnegative(),
});
export type WorkflowRun = z.infer<typeof workflowRunSchema>;

/** A step of a workflow run: an agent run (its log opens) or a step of the app itself. */
export const flowStepSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(["agent", "system"]),
  /** Its box in the workflow's graph, when it has one. */
  node: z.string().nullable(),
  name: z.string(),
  agent: z.string().nullable(),
  status: flowStatus,
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  duration_s: z.number().nullable(),
  /** A private run: its log keeps names and sizes, never the content. */
  private: z.boolean(),
  note: z.string().nullable(),
});
export type FlowStep = z.infer<typeof flowStepSchema>;

export const workflowRunDetailSchema = workflowRunSchema.extend({
  flow: agentFlowSchema.nullable(),
  steps: z.array(flowStepSchema),
  input_tokens: z.number().int().nullable(),
  output_tokens: z.number().int().nullable(),
  cost_usd: z.number().nullable(),
  credential_kind: z.enum(credentialKinds).nullable(),
  /** Why a failed run failed, in the words of its first failed step. */
  reason: z.string().nullable().catch(null),
  /** A job search's count of the postings it added. */
  new_postings: z.number().int().nonnegative().nullable().catch(null),
});
export type WorkflowRunDetail = z.infer<typeof workflowRunDetailSchema>;

const workflowListSchema = z.object({ items: z.array(workflowSchema) });
const workflowRunListSchema = z.object({ items: z.array(workflowRunSchema) });
const endedSchema = z.object({
  id: z.string().min(1),
  finished_at: z.string(),
});
export type EndedRun = z.infer<typeof endedSchema>;
const underWaySchema = z.object({
  under_way: z.number().int().nonnegative(),
  /** The runs that ended last, newest end first: the toast of a run's end reads them. */
  ended: z.array(endedSchema).catch([]),
});
export type ActivityCount = z.infer<typeof underWaySchema>;

export const activityKey = ["activity"] as const;
export const workflowsKey = [...activityKey, "workflows"] as const;
export const workflowRunsKey = (workflow: string | null) =>
  [...activityKey, "runs", workflow ?? "all"] as const;
export const workflowRunKey = (id: string) =>
  [...activityKey, "run", id] as const;
export const activityCountKey = [...activityKey, "count"] as const;

export async function listWorkflows(signal?: AbortSignal): Promise<Workflow[]> {
  const body = await request("/activity/workflows", {
    signal,
    validate: (data) => workflowListSchema.parse(data),
  });
  return body.items;
}

/** The person's workflow runs, newest first: all of them, or one workflow's. */
export async function listWorkflowRuns(
  workflow: string | null,
  signal?: AbortSignal,
): Promise<WorkflowRun[]> {
  const query = new URLSearchParams({ limit: "50" });
  if (workflow) query.set("workflow", workflow);
  const body = await request(`/activity/runs?${query.toString()}`, {
    signal,
    validate: (data) => workflowRunListSchema.parse(data),
  });
  return body.items;
}

/** One workflow run. `id` may also be the id of one of its agent runs (an old /runs/:id link). */
export function getWorkflowRun(
  id: string,
  signal?: AbortSignal,
): Promise<WorkflowRunDetail> {
  return request(`/activity/runs/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => workflowRunDetailSchema.parse(data),
  });
}

/** How many of the person's workflow runs are under way (running, queued or waiting), and
 * those that ended last. */
export function getActivityCount(signal?: AbortSignal): Promise<ActivityCount> {
  return request("/activity/count", {
    signal,
    timeoutMs: 15_000,
    validate: (data) => underWaySchema.parse(data),
  });
}
