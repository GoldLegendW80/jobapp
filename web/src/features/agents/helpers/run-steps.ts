/**
 * What the timeline of a run shows for each step: an icon, a title, what the step says and a
 * tone. Data only; components/run/run-timeline.tsx draws it.
 */
import { runErrorWords } from "@/lib/wording";

import type { CredentialKind, RunStatus, RunStep } from "../api/runs-api";
import {
  STATUS_LABEL,
  describeCost,
  formatTokens,
  kindOfUsage,
} from "./format";
import type { LiveText } from "./live-text";

/** The picture of a step; the timeline draws each with an icon of its own. */
export type StepIcon =
  | "failed"
  | "cancelled"
  | "succeeded"
  | "status"
  | "measure"
  | "thinking"
  | "answer"
  | "tool"
  | "problem"
  | "refused";

export type StepBody =
  /** a sentence */
  | { kind: "note"; text: string }
  /** the text of the answer, as the model wrote it */
  | { kind: "answer"; text: string }
  /** the arguments of a tool call, each name with its value */
  | { kind: "arguments"; entries: [string, string][] }
  /** a private run stores sizes, never the content: the sizes */
  | { kind: "size"; text: string }
  /** the result of a tool call, folded */
  | { kind: "result"; content: string };

export interface StepView {
  icon: StepIcon;
  title: string;
  body?: StepBody;
  tone?: "error" | "warning" | "muted";
}

export interface TimelineItem {
  key: string;
  kind: string;
  view: StepView;
  /** the answer being written, before its step is stored */
  live: boolean;
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString("en-US")} ${noun}${count === 1 ? "" : "s"}`;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value ?? "");
}

const toolName = (value: unknown) => text(value).replace(/^mcp__app__/, "");

/** An answer in the timeline: the stored step and the text being written look the same. */
export function answerView(value: string): StepView {
  return {
    icon: "answer",
    title: "Answer",
    body: { kind: "answer", text: value },
  };
}

function statusView(data: Record<string, unknown>): StepView {
  const status = data.status as RunStatus;
  const labels: Partial<Record<string, string>> = STATUS_LABEL;
  const label = labels[text(data.status)] ?? text(data.status);
  if (status === "failed") {
    return {
      icon: "failed",
      title: label,
      body: data.error
        ? { kind: "note", text: runErrorWords(text(data.error)) }
        : undefined,
      tone: "error",
    };
  }
  if (status === "cancelled") return { icon: "cancelled", title: label };
  if (status === "done") return { icon: "succeeded", title: label };
  return { icon: "status", title: label, tone: "muted" };
}

function toolCallView(data: Record<string, unknown>): StepView {
  const args =
    data.input && typeof data.input === "object"
      ? Object.entries(data.input as Record<string, unknown>)
      : [];
  const view: StepView = {
    icon: "tool",
    title: `Tool call: ${toolName(data.name)}`,
  };
  if (args.length) {
    view.body = {
      kind: "arguments",
      entries: args.map(([key, value]) => [key, text(value)]),
    };
  } else if (typeof data.input_size === "number") {
    // A private run stores the size of the arguments, never the arguments.
    view.body = {
      kind: "size",
      text: `${plural(data.input_size, "character")} of arguments`,
    };
  }
  return view;
}

function toolResultView(data: Record<string, unknown>): StepView {
  const failed = data.is_error === true;
  const view: StepView = {
    icon: failed ? "problem" : "succeeded",
    title: failed ? "Tool error" : "Tool result",
    tone: failed ? "error" : undefined,
  };
  if (data.content === undefined && typeof data.size === "number") {
    // A private run stores the size of a result and its non-text blocks, never the content:
    // show them, and no box for a content that is not there.
    const blocks =
      typeof data.blocks === "number" && data.blocks > 0
        ? `, ${plural(data.blocks, "other block")}`
        : "";
    view.body = {
      kind: "size",
      text: `${plural(data.size, "character")}${blocks}`,
    };
  } else {
    view.body = { kind: "result", content: text(data.content) };
  }
  return view;
}

function usageView(
  data: Record<string, unknown>,
  kind: CredentialKind | null | undefined,
): StepView {
  const tokens = formatTokens(
    Number(data.input_tokens ?? 0) +
      Number(data.cache_creation_input_tokens ?? 0) +
      Number(data.cache_read_input_tokens ?? 0),
    Number(data.output_tokens ?? 0),
  );
  const cost = describeCost(
    typeof data.cost_usd === "number" ? data.cost_usd : null,
    // The step says which kind of credential the run used; older steps do not.
    kindOfUsage(data) ?? kind,
  );
  return {
    icon: "measure",
    title: "Usage",
    body: { kind: "note", text: `${tokens}, ${cost}` },
    tone: "muted",
  };
}

/** One step as the timeline shows it, or null for a step it does not show. */
export function describeStep(
  step: RunStep,
  kind: CredentialKind | null | undefined,
): StepView | null {
  const data = step.data;
  switch (step.kind) {
    case "status":
      return statusView(data);
    case "init": {
      const tools = Array.isArray(data.tools) ? data.tools.map(toolName) : [];
      return {
        icon: "measure",
        title: `Started on ${text(data.model)}`,
        body: {
          kind: "note",
          text: tools.length ? `Tools: ${tools.join(", ")}` : "No tools",
        },
        tone: "muted",
      };
    }
    case "thinking":
      return { icon: "thinking", title: "Thinking", tone: "muted" };
    case "text":
      return answerView(text(data.text));
    case "tool_call":
      return toolCallView(data);
    case "tool_result":
      return toolResultView(data);
    case "tool_denied":
      return {
        icon: "refused",
        title: `Refused: ${text(data.name)}`,
        body: {
          kind: "note",
          text: "This tool is not available to this agent.",
        },
        tone: "warning",
      };
    case "usage":
      return usageView(data, kind);
    case "api_error":
      return {
        icon: "problem",
        title: "Model API error",
        body: { kind: "note", text: text(data.error) },
        tone: "error",
      };
    case "rate_limit":
      return {
        icon: "problem",
        title: "Rate limit",
        body: { kind: "note", text: text(data.status) },
        tone: "warning",
      };
    default:
      return null;
  }
}

/** The items of the timeline: the steps it shows, then the answer being written, last. Its
 * item is the stored step's, before the step exists. */
export function timelineItems(
  steps: RunStep[],
  live: LiveText | null,
  kind: CredentialKind | null | undefined,
): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const step of steps) {
    const view = describeStep(step, kind);
    if (view !== null) {
      items.push({ key: String(step.seq), kind: step.kind, view, live: false });
    }
  }
  if (live?.text) {
    items.push({
      key: "live",
      kind: "text",
      view: answerView(live.text),
      live: true,
    });
  }
  return items;
}
