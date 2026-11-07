import type { Editable, EditableField } from "../api/agents-api";
import { exactTime } from "./relative-time";

/** How the agent page names each kind of credential a run can use. */
export const CREDENTIAL_KIND_LABEL: Record<string, string> = {
  anthropic_api_key: "With an API key",
  claude_subscription: "With a Claude subscription",
};

/** How "The code's <field> changed" names each editable field. */
export const FIELD_LABEL: Record<EditableField, string> = {
  description: "description",
  system_prompt: "prompt",
  default_message: "default message",
  model: "model",
  model_by_credential: "model per credential kind",
  effort: "effort",
  max_turns: "max turns",
  wall_time_s: "wall time",
  tools: "tools",
};

/** When a run started, or that it has not yet (a queued run). */
export const whenStarted = (at: string | null) =>
  at === null ? "Not started" : exactTime(at);

/** A code's value in words, for the notice of a field the code changed. */
export function codeValueInWords(value: Editable[EditableField]): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (value === null) return "Claude Code's default";
  if (typeof value === "object") {
    const kinds = Object.entries(value).map(
      ([kind, model]) => `${CREDENTIAL_KIND_LABEL[kind] ?? kind}, ${model}`,
    );
    return kinds.length ? kinds.join("; ") : "the default model";
  }
  return value === "" ? "none" : String(value);
}

/** The pages above one agent's page: Admin, then its Agents view. */
export const AGENT_TRAIL = [
  { to: "/admin/users", label: "Admin" },
  { to: "/admin/agents", label: "Agents" },
];
