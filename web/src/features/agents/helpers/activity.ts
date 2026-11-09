/**
 * The Activity page's rules, no React: a workflow run's statuses on its graph, and an agent
 * step's log cut into the collapsible groups GitHub Actions shows for a job's steps.
 */
import type { AgentFlow } from "@/features/admin/api/agents-api";

import {
  ACTIVE_FLOW_STATUSES,
  type FlowStatus,
  type FlowStep,
  type WorkflowRunDetail,
} from "../api/activity-api";
import type { RunStep } from "../api/runs-api";
import { formatDuration } from "./format";
import type { StepView, TimelineItem } from "./run-steps";

export const FLOW_STATUS_WORDS: Record<FlowStatus, string> = {
  waiting: "Waiting",
  queued: "Queued",
  running: "Running",
  success: "Succeeded",
  failure: "Failed",
  skipped: "Skipped",
  cancelled: "Cancelled",
};

// A box that holds several steps shows the most telling one: a failure, then work going on.
const TELLING: FlowStatus[] = [
  "failure",
  "running",
  "queued",
  "waiting",
  "cancelled",
  "success",
  "skipped",
];

/** The seconds a step or a run has taken, counted to `now` while it goes on. */
export function secondsOf(
  item: Pick<FlowStep, "started_at" | "finished_at" | "duration_s">,
  now: number,
): number | null {
  if (item.duration_s !== null) return item.duration_s;
  if (!item.started_at) return null;
  const start = Date.parse(item.started_at);
  const end = item.finished_at ? Date.parse(item.finished_at) : now;
  return Number.isNaN(start) ? null : Math.max(0, (end - start) / 1000);
}

export function durationWords(seconds: number | null): string | undefined {
  return seconds === null ? undefined : formatDuration(seconds);
}

/** Where the page opens an agent step: the run's own page with that step's log. */
export const stepHref = (runId: string, stepId: string) =>
  `/activity/runs/${encodeURIComponent(runId)}?step=${encodeURIComponent(stepId)}`;

/** The boxes reached from `from` by forward arrows: a return never makes a step "to come". */
function downstream(flow: AgentFlow, from: Set<string>): Set<string> {
  const column = new Map(flow.nodes.map((node) => [node.id, node.column]));
  const reached = new Set<string>();
  const queue = [...from];
  while (queue.length) {
    const id = queue.shift() ?? "";
    for (const edge of flow.edges) {
      if (edge.from !== id || reached.has(edge.to) || from.has(edge.to))
        continue;
      if ((column.get(edge.to) ?? 0) < (column.get(id) ?? 0)) continue;
      reached.add(edge.to);
      queue.push(edge.to);
    }
  }
  return reached;
}

export interface NodeState {
  status?: FlowStatus;
  duration?: string;
  href?: string;
  count?: number;
}

/** Several steps as one: the most telling status, the time from the first start to the last end. */
function stateOf(runId: string, steps: FlowStep[], now: number): NodeState {
  const statuses = steps.map((step) => step.status);
  const starts = steps
    .map((step) => (step.started_at ? Date.parse(step.started_at) : NaN))
    .filter((at) => !Number.isNaN(at));
  const ends = steps.map((step) => {
    const seconds = secondsOf(step, now);
    return step.started_at && seconds !== null
      ? Date.parse(step.started_at) + seconds * 1000
      : NaN;
  });
  const end = Math.max(...ends.filter((at) => !Number.isNaN(at)));
  const span = starts.length ? (end - Math.min(...starts)) / 1000 : null;
  const agentSteps = steps.filter((step) => step.kind === "agent");
  // One agent step opens its log; several (a website each) open the first still going.
  const opens =
    agentSteps.find((step) => ACTIVE_FLOW_STATUSES.has(step.status)) ??
    agentSteps.at(0);
  return {
    status: TELLING.find((status) => statuses.includes(status)),
    duration: durationWords(
      span !== null && Number.isFinite(span) ? span : null,
    ),
    href: opens ? stepHref(runId, opens.id) : undefined,
    count: steps.length,
  };
}

function stepsByNode(run: WorkflowRunDetail): Map<string, FlowStep[]> {
  const byNode = new Map<string, FlowStep[]>();
  for (const step of run.steps) {
    if (!step.node) continue;
    byNode.set(step.node, [...(byNode.get(step.node) ?? []), step]);
  }
  return byNode;
}

/**
 * Each box of the run's graph with its status, duration and link: the box of each step from its
 * steps, the trigger's box as done, and while the run goes on the agents and app steps it can
 * still reach as waiting. Person and data boxes the run did not touch stay plain.
 */
export function nodeStates(
  run: WorkflowRunDetail,
  now: number,
): Record<string, NodeState> {
  const flow = run.flow;
  if (!flow) return {};
  const states: Record<string, NodeState> = {};
  for (const [node, steps] of stepsByNode(run)) {
    states[node] = stateOf(run.id, steps, now);
  }
  if (run.trigger.node && !states[run.trigger.node]) {
    states[run.trigger.node] = { status: "success" };
  }
  if (ACTIVE_FLOW_STATUSES.has(run.status)) {
    const kinds = new Map(flow.nodes.map((node) => [node.id, node.kind]));
    const started = new Set(Object.keys(states));
    for (const id of downstream(flow, started)) {
      const kind = kinds.get(id);
      if (kind === "agent" || kind === "system")
        states[id] = { status: "waiting" };
    }
  }
  return states;
}

/**
 * Each group card of the run's graph (the hiring panel's reviewers) by its name: the most telling
 * status of its agents' steps and its total time, from the first start to the last end.
 */
export function groupStates(
  run: WorkflowRunDetail,
  now: number,
): Record<string, NodeState> {
  const flow = run.flow;
  if (!flow) return {};
  const byNode = stepsByNode(run);
  const members = new Map<string, FlowStep[]>();
  for (const node of flow.nodes) {
    if (node.kind !== "agent" || !node.group) continue;
    const steps = byNode.get(node.id) ?? [];
    members.set(node.group, [...(members.get(node.group) ?? []), ...steps]);
  }
  const states: Record<string, NodeState> = {};
  for (const [group, steps] of members) {
    // Its agents' rows say how many times each ran: the card says no count of its own.
    if (steps.length)
      states[group] = { ...stateOf(run.id, steps, now), count: undefined };
  }
  return states;
}

/** One collapsible group of an agent step's log, as a step of a GitHub Actions job. */
export interface LogGroup {
  key: string;
  head: StepView;
  at: string | null;
  items: TimelineItem[];
  /** Opened at first: all but a tool call (its arguments and result) and thinking. */
  open: boolean;
  live: boolean;
}

const SETUP_KINDS = new Set(["status", "init"]);

/**
 * The log's groups, oldest first: "Set up" (queued, started, the model and its tools), then each
 * answer, thinking, refusal and problem on its own, each tool call with its result, and the
 * usage and the end. A status after the first answer or call is the end, its own group.
 */
export function logGroups(steps: RunStep[], items: TimelineItem[]): LogGroup[] {
  const atOf = new Map(steps.map((step) => [String(step.seq), step.at]));
  const groups: LogGroup[] = [];
  let setup: LogGroup | null = null;
  for (const item of items) {
    const at = atOf.get(item.key) ?? null;
    const last = groups.at(-1);
    if (SETUP_KINDS.has(item.kind) && (groups.length === 0 || last === setup)) {
      if (!setup) {
        setup = {
          key: "setup",
          head: { icon: "status", title: "Set up" },
          at,
          items: [],
          open: true,
          live: false,
        };
        groups.push(setup);
      }
      setup.items.push(item);
      continue;
    }
    if (item.kind === "tool_result" && last?.items[0]?.kind === "tool_call") {
      last.items.push(item);
      if (item.view.tone === "error") last.open = true;
      continue;
    }
    groups.push({
      key: item.key,
      head: item.view,
      at,
      items: [item],
      open: item.kind !== "tool_call" && item.kind !== "thinking",
      live: item.live,
    });
  }
  return groups;
}

function wordsOf(view: StepView): string {
  const body = view.body;
  if (!body) return view.title;
  switch (body.kind) {
    case "arguments":
      return `${view.title} ${body.entries.flat().join(" ")}`;
    case "result":
      return `${view.title} ${body.content}`;
    default:
      return `${view.title} ${body.text}`;
  }
}

/** The groups a filter keeps: those whose words hold it, whatever the case. */
export function filterGroups(groups: LogGroup[], filter: string): LogGroup[] {
  const wanted = filter.trim().toLowerCase();
  if (!wanted) return groups;
  return groups.filter((group) =>
    [group.head, ...group.items.map((item) => item.view)].some((view) =>
      wordsOf(view).toLowerCase().includes(wanted),
    ),
  );
}

/** A log line's time from the run's start, as GitHub shows it: 0:05, 12:40, 1:02:03. */
export function offsetWords(at: string | null, start: string | null): string {
  if (!at || !start) return "";
  const seconds = Math.max(
    0,
    Math.floor((Date.parse(at) - Date.parse(start)) / 1000),
  );
  if (Number.isNaN(seconds)) return "";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}`
    : `${minutes}:${rest}`;
}
