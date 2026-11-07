/**
 * Calls of /api/agents/threads (agents A4, SPEC-agents.md, API): the chat about one job. A thread
 * is found by its job (one per job and user), created on the first message, and read with its
 * messages; each message starts a run of the chat agent, followed live with the runs stream
 * (runs-api.ts `followRun`, through `useRunStream`).
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

import { runSchema, runStatuses } from "./runs-api";

export const MESSAGE_MAX = 8000;

export const threadSchema = z.object({
  id: z.string().min(1),
  job_id: z.string().nullable(),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Thread = z.infer<typeof threadSchema>;

export const messageSchema = z.object({
  id: z.number().int(),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  run_id: z.string().nullable(),
  run_status: z.enum(runStatuses).nullable().optional(),
  error: z.string().nullable().optional(),
  at: z.string(),
});
export type ChatMessage = z.infer<typeof messageSchema>;

export const threadDetailSchema = threadSchema.extend({
  messages: z.array(messageSchema),
  messages_truncated: z.boolean(),
  active_run_id: z.string().nullable(),
});
export type ThreadDetail = z.infer<typeof threadDetailSchema>;

const threadListSchema = z.object({ items: z.array(threadSchema) });
const sentSchema = z.object({ message: messageSchema, run: runSchema });
export type Sent = z.infer<typeof sentSchema>;

export const jobThreadKey = (jobId: string) =>
  ["agent-threads", "job", jobId] as const;
export const threadKey = (id: string) => ["agent-threads", id] as const;

/** The user's thread on this job, or null when there is none yet. */
export async function findJobThread(
  jobId: string,
  signal?: AbortSignal,
): Promise<Thread | null> {
  const query = new URLSearchParams({ job_id: jobId, limit: "1" });
  const body = await request(`/agents/threads?${query.toString()}`, {
    signal,
    validate: (data) => threadListSchema.parse(data),
  });
  return body.items[0] ?? null;
}

/** The thread on this job: the existing one, or a new one. */
export function openJobThread(jobId: string): Promise<Thread> {
  return request("/agents/threads", {
    method: "POST",
    body: { job_id: jobId },
    validate: (data) => threadSchema.parse(data),
  });
}

export function getThread(
  id: string,
  signal?: AbortSignal,
): Promise<ThreadDetail> {
  return request(`/agents/threads/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => threadDetailSchema.parse(data),
  });
}

export function sendMessage(threadId: string, text: string): Promise<Sent> {
  return request(`/agents/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    body: { text },
    validate: (data) => sentSchema.parse(data),
  });
}

export async function deleteThread(id: string): Promise<void> {
  await request(`/agents/threads/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** The run still answering, from a 409 of sendMessage, if the error is that one. */
export function busyRunId(error: unknown): string | null {
  if (!isApiError(error) || error.status !== 409) return null;
  const body = error.body as { active_run_id?: unknown } | undefined;
  return typeof body?.active_run_id === "string" ? body.active_run_id : null;
}
