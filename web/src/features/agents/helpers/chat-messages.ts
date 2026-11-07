/** Pure readings of a job's conversation: which message got no answer, the newest answer, and
 * what the agent is doing while its answer has no text yet. */
import type { RunStep } from "../api/runs-api";
import type { ChatMessage, ThreadDetail } from "../api/chat-api";

/** A message of the reader whose run failed or was stopped before it stored an answer. */
export function unanswered(
  message: ChatMessage,
  messages: ChatMessage[],
): boolean {
  if (message.role !== "user") return false;
  if (message.run_status !== "failed" && message.run_status !== "cancelled")
    return false;
  return !messages.some(
    (other) => other.role === "assistant" && other.run_id === message.run_id,
  );
}

export function newestAnswer(
  detail: ThreadDetail | undefined,
): ChatMessage | null {
  if (!detail) return null;
  return (
    [...detail.messages].reverse().find((m) => m.role === "assistant") ?? null
  );
}

/** What the agent is doing, from its newest step, while no answer text has arrived. */
export function replyActivity(steps: RunStep[], queued: boolean): string {
  if (queued) return "Waiting for a free agent";
  const last = steps.at(-1);
  if (last?.kind === "tool_call") {
    const name = typeof last.data.name === "string" ? last.data.name : "";
    if (name.endsWith("get_job")) return "Reading the posting";
    if (name.endsWith("get_profile_section")) return "Reading your profile";
  }
  return "Thinking";
}
