/**
 * What the timeline of a run shows for each step: an icon, a title, what the step says and a
 * tone. Data only; components/shared/agents/run/timeline-step.tsx draws it.
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

/** The preview a private run keeps of an app tool's call (api runner.preview_args, browser
 * B25): an address or a pattern shown as is, a short word, a number or a switch, and of a typed
 * text, a script or a list only its length (`<name>_length`) or count (`<name>_count`). */
function previewOf(data: Record<string, unknown>): Record<string, unknown> {
  return data.preview && typeof data.preview === "object"
    ? (data.preview as Record<string, unknown>)
    : {};
}

function previewEntry([key, value]: [string, unknown]): [string, string] {
  const length = /^(.+)_length$/.exec(key);
  if (length?.[1] && typeof value === "number")
    return [length[1], plural(value, "character")];
  const count = /^(.+)_count$/.exec(key);
  if (count?.[1] && typeof value === "number")
    return [count[1], plural(value, "item")];
  return [key, text(value)];
}

/** Claude Code's web search (jobs J21): a private run keeps its query in `preview.query`
 * (api runner.Recorder, 200 characters at most) and its answer's link count in `results`. */
const WEB_SEARCH = "WebSearch";

function toolCallView(data: Record<string, unknown>): StepView {
  const args =
    data.input && typeof data.input === "object"
      ? Object.entries(data.input as Record<string, unknown>)
      : [];
  const preview = previewOf(data);
  if (data.name === WEB_SEARCH) {
    const query = [preview.query, Object.fromEntries(args).query].find(
      (value): value is string => typeof value === "string" && value !== "",
    );
    if (query) return { icon: "tool", title: `Web search: ${query}` };
  }
  // The address of a navigation, or the pattern of a find, follows the tool's name.
  const subject = [preview.url, preview.pattern].find(
    (value): value is string => typeof value === "string" && value !== "",
  );
  const view: StepView = {
    icon: "tool",
    title: `Tool call: ${toolName(data.name)}${subject ? ` ${subject}` : ""}`,
  };
  const shown = Object.entries(preview).filter(
    ([key]) => key !== "url" && key !== "pattern",
  );
  if (!args.length && shown.length) {
    view.body = { kind: "arguments", entries: shown.map(previewEntry) };
    return view;
  }
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
  // A private run keeps the error text of an app tool (browser B25), never its content.
  const error =
    failed && typeof data.error === "string" && data.error !== ""
      ? data.error
      : null;
  const view: StepView = {
    icon: failed ? "problem" : "succeeded",
    title: error
      ? `Tool error: ${error}`
      : failed
        ? "Tool error"
        : "Tool result",
    tone: failed ? "error" : undefined,
  };
  if (!failed && typeof data.results === "number") {
    // A web search's answer: how many results it listed, never their text (jobs J21).
    view.title = plural(data.results, "result");
    return view;
  }
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
  // Jobs J27: a follow up is a new query of the same session. Its result gives that query's
  // tokens but the session's cost so far, so the step says which is which.
  const followUps = Number(data.follow_ups ?? 0);
  if (followUps > 0) {
    return {
      icon: "measure",
      title: `Usage of follow up ${followUps}`,
      body: {
        kind: "note",
        text: `This query: ${tokens}. Total so far: ${cost}`,
      },
      tone: "muted",
    };
  }
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
    // The app continued the session: the agent ended its turn with work left (jobs J27).
    case "follow_up":
      return {
        icon: "measure",
        title: `Asked to go on (${Number(data.attempt ?? 0)} of ${Number(data.of ?? 0)})`,
        body: {
          kind: "note",
          text: "The assistant ended its turn with searches of its plan left, so the app asked it to go on.",
        },
        tone: "muted",
      };
    case "unfinished":
      return {
        icon: "problem",
        title: "Ended with its plan unfinished",
        body: {
          kind: "note",
          text: "The assistant ended its turn again after the app asked it to go on. What it found is kept.",
        },
        tone: "warning",
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
