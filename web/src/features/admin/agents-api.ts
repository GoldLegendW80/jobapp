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
});
export type AgentSummary = z.infer<typeof agentSummarySchema>;

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
      created_at: z.string(),
      finished_at: z.string().nullable(),
      mine: z.boolean(),
    }),
  ),
  choices: z.object({
    models: z.array(z.string()),
    efforts: z.array(z.string()),
    credential_kinds: z.array(z.string()),
    turns_max: z.number().int(),
    wall_time_min_s: z.number(),
    wall_time_max_s: z.number(),
    prompt_max: z.number().int(),
    description_max: z.number().int(),
    message_max: z.number().int(),
    note_max: z.number().int(),
  }),
});
export type AgentPage = z.infer<typeof agentPageSchema>;

export const agentsKey = ["admin", "agents"] as const;
export const agentKey = (name: string) => [...agentsKey, name] as const;

export function listAgents(signal?: AbortSignal): Promise<AgentSummary[]> {
  return request("/admin/agents", {
    signal,
    validate: (data) =>
      z.object({ items: z.array(agentSummarySchema) }).parse(data).items,
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
    found.wall_time_s = `From ${String(choices.wall_time_min_s)} to ${String(choices.wall_time_max_s)} seconds: never more than the code gives.`;
  return found;
}
