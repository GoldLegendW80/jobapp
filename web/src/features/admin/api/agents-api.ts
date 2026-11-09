/**
 * Calls of /api/admin/agents (agents A6, SPEC-agents.md "The Agents tab"): every agent of the
 * app as the running code defines it plus what an admin saved, and the save of a new version.
 * Bodies are checked with zod.
 */
import { z } from "zod";

import { request } from "@/lib/api";

export const agentSummarySchema = z.object({
  name: z.string().min(1),
  description: z.string(),
  module: z.string(),
  model: z.string(),
  test_only: z.boolean(),
  internal: z.boolean(),
  private_io: z.boolean(),
  version: z.number().int().nullable(),
  overridden: z.boolean(),
  /** The fields of that version whose code value changed since it was saved. */
  code_changed: z.array(z.string()),
  last_run_at: z.string().nullable(),
  /** The key of its group in the list (agents A8); "" from an api that has none. */
  category: z.string().default(""),
});
export type AgentSummary = z.infer<typeof agentSummarySchema>;

export const FLOW_NODE_KINDS = ["agent", "person", "system", "data"] as const;

/**
 * A box of a category's workflow (catalog.FLOWS, agents A9): an agent, whose id is its name, or
 * a person's click, a job of the app, or stored data, at a column and a row laid out by the api.
 */
export const flowNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(FLOW_NODE_KINDS).catch("system"),
  label: z.string(),
  column: z.number().int().min(0),
  row: z.number().int().min(0),
  note: z.string().default(""),
  /** Agents that run side by side under one step share it: one card lists them. */
  group: z.string().default(""),
});
export type FlowNode = z.infer<typeof flowNodeSchema>;

/** An arrow of a workflow: who starts or feeds whom, in a few words. */
export const flowEdgeSchema = z.object({
  from: z.string(),
  to: z.string(),
  label: z.string(),
});
export type FlowEdge = z.infer<typeof flowEdgeSchema>;

export const agentFlowSchema = z.object({
  nodes: z.array(flowNodeSchema),
  edges: z.array(flowEdgeSchema),
});
export type AgentFlow = z.infer<typeof agentFlowSchema>;

/** A group of the list, in the api's order (catalog.CATEGORIES), with its workflow. */
export const agentCategorySchema = z.object({
  key: z.string().min(1),
  title: z.string().min(1),
  sentence: z.string(),
  /** None for the test agents, from an api older than the diagrams, and for a malformed one. */
  flow: agentFlowSchema.nullable().catch(null),
});
export type AgentCategory = z.infer<typeof agentCategorySchema>;

export const agentListSchema = z.object({
  items: z.array(agentSummarySchema),
  categories: z.array(agentCategorySchema).default([]),
});
export type AgentList = z.infer<typeof agentListSchema>;

const editableSchema = z.object({
  description: z.string(),
  system_prompt: z.string(),
  default_message: z.string(),
  model: z.string(),
  model_by_credential: z.record(z.string(), z.string()),
  effort: z.string().nullable(),
  max_turns: z.number().int(),
  wall_time_s: z.number(),
  tools: z.array(z.string()),
});
export type Editable = z.infer<typeof editableSchema>;
export type EditableField = keyof Editable;

export const agentPageSchema = z.object({
  name: z.string().min(1),
  module: z.string(),
  starter: z.string(),
  flags: z.array(z.string()),
  test_only: z.boolean(),
  internal: z.boolean(),
  private_io: z.boolean(),
  code: editableSchema,
  saved: editableSchema.partial(),
  effective: editableSchema,
  input_fields: z.array(
    z.object({
      name: z.string(),
      type: z.string(),
      required: z.boolean(),
      default: z.unknown(),
      limits: z.string(),
      description: z.string(),
    }),
  ),
  input_schema: z.record(z.string(), z.unknown()),
  first_message: z.string(),
  /** Whether a run's first message can be the default message: editable only then. */
  uses_default_message: z.boolean(),
  tools: z.array(
    z.object({
      name: z.string(),
      full_name: z.string(),
      module: z.string().nullable(),
      description: z.string().nullable(),
      input_schema: z.record(z.string(), z.unknown()).nullable(),
      described_at: z.string().nullable(),
      on: z.boolean(),
    }),
  ),
  turn_budgeted: z.boolean(),
  ends: z.array(z.string()),
  claude_code: z.record(z.string(), z.unknown()),
  environment: z.string(),
  gate: z.string(),
  skills: z.string(),
  guards: z.string(),
  version: z.number().int().nullable(),
  /** The fields of the version that runs whose code value changed since it was saved. */
  code_changed: z.array(z.string()),
  /** The code's prompt when that version was saved, when the code's prompt changed since. */
  code_prompt_then: z.string().nullable(),
  versions: z.array(
    z.object({
      version: z.number().int(),
      fields: z.array(z.string()),
      saved_by: z.string().nullable(),
      saved_at: z.string(),
      note: z.string().nullable(),
    }),
  ),
  runs: z.array(
    z.object({
      id: z.string(),
      status: z.string(),
      model: z.string().nullable(),
      version: z.number().int().nullable(),
      turns: z.number().int().nullable(),
      input_tokens: z.number().int().nullable(),
      output_tokens: z.number().int().nullable(),
      /** When it was queued. */
      created_at: z.string(),
      /** Null: not started yet. */
      started_at: z.string().nullable(),
      finished_at: z.string().nullable(),
      /** The person the run is for, as the Users page shows them: the name, else the e-mail. */
      user_name: z.string(),
      user_email: z.string(),
      mine: z.boolean(),
    }),
  ),
  choices: z.object({
    models: z.array(z.string()),
    efforts: z.array(z.string()),
    credential_kinds: z.array(z.string()),
    turns_max: z.number().int(),
    wall_time_min_s: z.number(),
    /** The agent's own ceiling: the bound of the job that runs it (A7). */
    wall_time_max_s: z.number(),
    /** One sentence saying where the agent runs, the job whose bound is that ceiling. */
    wall_time_where: z.string(),
    prompt_max: z.number().int(),
    description_max: z.number().int(),
    message_max: z.number().int(),
    note_max: z.number().int(),
  }),
});
export type AgentPage = z.infer<typeof agentPageSchema>;

export const agentsKey = ["admin", "agents"] as const;
export const agentKey = (name: string) => [...agentsKey, name] as const;

export function listAgents(signal?: AbortSignal): Promise<AgentList> {
  return request("/admin/agents", {
    signal,
    validate: (data) => agentListSchema.parse(data),
  });
}

export function readAgent(
  name: string,
  signal?: AbortSignal,
): Promise<AgentPage> {
  return request(`/admin/agents/${encodeURIComponent(name)}`, {
    signal,
    validate: (data) => agentPageSchema.parse(data),
  });
}

export interface NewVersion {
  changes: Partial<Editable>;
  note: string | null;
  /** The latest version the page showed (null: none). */
  base_version: number | null;
}

export function saveVersion(
  name: string,
  body: NewVersion,
): Promise<AgentPage> {
  return request(`/admin/agents/${encodeURIComponent(name)}/versions`, {
    method: "POST",
    body,
    validate: (data) => agentPageSchema.parse(data),
  });
}

/** The fields of the draft that differ from the code: what a save sends. */
export function changesOf(code: Editable, draft: Editable): Partial<Editable> {
  const changes: Partial<Editable> = {};
  for (const key of Object.keys(code) as EditableField[]) {
    if (
      JSON.stringify(sorted(draft[key])) !== JSON.stringify(sorted(code[key]))
    )
      Object.assign(changes, { [key]: draft[key] });
  }
  return changes;
}

/** Object keys in one order, so two mappings compare by content. */
function sorted(value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value))
    return Object.fromEntries(
      Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
    );
  return value;
}

export const REVERT_NOTE = "Reverted to the code's version";

/** What the draft cannot be saved with, by field. */
export function problemsOf(draft: Editable, choices: AgentPage["choices"]) {
  const found: Partial<Record<keyof Editable, string>> = {};
  if (!draft.system_prompt.trim())
    found.system_prompt = "The prompt cannot be empty.";
  else if (draft.system_prompt.length > choices.prompt_max)
    found.system_prompt = `At most ${choices.prompt_max.toLocaleString("en")} characters.`;
  if (!draft.description.trim())
    found.description = "The description cannot be empty.";
  else if (draft.description.length > choices.description_max)
    found.description = `At most ${String(choices.description_max)} characters.`;
  if (draft.default_message.length > choices.message_max)
    found.default_message = `At most ${String(choices.message_max)} characters.`;
  if (
    !Number.isInteger(draft.max_turns) ||
    draft.max_turns < 1 ||
    draft.max_turns > choices.turns_max
  )
    found.max_turns = `A whole number from 1 to ${choices.turns_max.toLocaleString("en")}.`;
  if (
    !Number.isFinite(draft.wall_time_s) ||
    draft.wall_time_s < choices.wall_time_min_s ||
    draft.wall_time_s > choices.wall_time_max_s
  )
    found.wall_time_s = `From ${String(choices.wall_time_min_s)} to ${choices.wall_time_max_s.toLocaleString("en")} seconds: never more than the job this agent runs in allows.`;
  return found;
}

/** A number of seconds in words: "24 hours", "15 minutes", "45 seconds". */
export function duration(seconds: number): string {
  const [size, unit] =
    seconds >= 3600
      ? [seconds / 3600, "hour"]
      : seconds >= 60
        ? [seconds / 60, "minute"]
        : [seconds, "second"];
  const shown = Number(size.toFixed(2));
  return `${shown.toLocaleString("en")} ${unit}${shown === 1 ? "" : "s"}`;
}
