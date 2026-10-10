/**
 * Calls of /api/agents (SPEC-agents.md, API): the agents available, the user's runs, starting
 * and cancelling one, and `followRun`, which streams a run's steps from
 * GET /api/agents/runs/{id}/stream.
 *
 * The stream replays the run's stored steps after `after`, then the new ones as the worker writes
 * them, and ends with an `end` event. Every step carries its `seq` as the event id, so a
 * reconnect (EventSource's own, after a network blip, with the Last-Event-ID header; or ours,
 * with `?after=`, when the browser gave up because the server answered an error) resumes exactly
 * where it stopped: steps are never lost and never shown twice.
 *
 * While an answer is being written the stream also sends `partial` events, the text so far of
 * the block being written (issue #33). They carry no id, so they never move the resume point;
 * live-text.ts says how they are applied.
 */
import { z } from "zod";

import { API_BASE, isApiError, request } from "@/lib/api";

export const runStatuses = [
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof runStatuses)[number];
export const FINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "done",
  "failed",
  "cancelled",
]);

export const runSchema = z.object({
  id: z.string().min(1),
  agent: z.string(),
  job_id: z.string().nullable(),
  status: z.enum(runStatuses),
  input: z.record(z.string(), z.unknown()),
  output: z.record(z.string(), z.unknown()).nullable(),
  error: z.string().nullable(),
  model: z.string().nullable(),
  input_tokens: z.number().int().nullable(),
  output_tokens: z.number().int().nullable(),
  cost_usd: z.number().nullable(),
  cancel_requested: z.boolean(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
  duration_s: z.number().nullable(),
  /** The job the run worked on (A3), null without one or once it is deleted. */
  job: z
    .object({ id: z.string(), company: z.string(), title: z.string() })
    .nullable()
    .optional(),
});
export type Run = z.infer<typeof runSchema>;

export const stepSchema = z.object({
  seq: z.number().int().positive(),
  at: z.string(),
  kind: z.string(),
  data: z.record(z.string(), z.unknown()),
});
export type RunStep = z.infer<typeof stepSchema>;

/** A piece of the text being written (live-text.ts `TextPart`). */
export const partialSchema = z.object({
  block: z.number().int().nonnegative(),
  after: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  text: z.string(),
});

export const runDetailSchema = runSchema.extend({
  events: z.array(stepSchema),
  events_truncated: z.boolean(),
});
export type RunDetail = z.infer<typeof runDetailSchema>;

export const runListSchema = z.object({ items: z.array(runSchema) });

export const agentSchema = z.object({
  name: z.string(),
  description: z.string(),
  model: z.string(),
  max_turns: z.number().int(),
  wall_time_s: z.number(),
  test_only: z.boolean(),
  needs_job: z.boolean().optional(),
});
export type Agent = z.infer<typeof agentSchema>;
const agentListSchema = z.object({ items: z.array(agentSchema) });

export const runsKey = ["agent-runs"] as const;
export const runKey = (id: string) => ["agent-runs", id] as const;
export const agentsKey = ["agents"] as const;

export async function listAgents(signal?: AbortSignal): Promise<Agent[]> {
  const body = await request("/agents", {
    signal,
    validate: (data) => agentListSchema.parse(data),
  });
  return body.items;
}

export const jobRunsKey = (jobId: string, agent: string) =>
  [...runsKey, "job", jobId, agent] as const;

/** The newest run of `agent` on one job (A3: the job's latest assessment), or none. */
export async function latestJobRun(
  jobId: string,
  agent: string,
  signal?: AbortSignal,
): Promise<Run | null> {
  const query = new URLSearchParams({ job_id: jobId, agent, limit: "1" });
  const body = await request(`/agents/runs?${query.toString()}`, {
    signal,
    validate: (data) => runListSchema.parse(data),
  });
  return body.items[0] ?? null;
}

export async function listRuns(signal?: AbortSignal): Promise<Run[]> {
  const body = await request("/agents/runs?limit=50", {
    signal,
    validate: (data) => runListSchema.parse(data),
  });
  return body.items;
}

export function getRun(id: string, signal?: AbortSignal): Promise<RunDetail> {
  return request(`/agents/runs/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => runDetailSchema.parse(data),
  });
}

export function startRun(body: {
  agent: string;
  job_id?: string;
  input: Record<string, unknown>;
}): Promise<Run> {
  return request("/agents/runs", {
    method: "POST",
    body,
    validate: (data) => runSchema.parse(data),
  });
}

export function cancelRun(id: string): Promise<Run> {
  return request(`/agents/runs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    validate: (data) => runSchema.parse(data),
  });
}

/* --- the live stream ------------------------------------------------------------------------ */

/** live: steps arrive as they happen. reconnecting: the connection dropped, it is coming back. */
export type Connection = "connecting" | "live" | "reconnecting" | "ended";

export interface FollowHandlers {
  onStep: (step: RunStep) => void;
  /** A piece of the answer being written; not a step, it has no seq. */
  onPartial?: (part: z.infer<typeof partialSchema>) => void;
  onConnection: (state: Connection) => void;
  /** The run was deleted, or is not (or no longer) the user's. */
  onGone?: () => void;
}

export const followTiming = { retryMs: 1000, maxRetryMs: 15_000 };

/**
 * Follow a run's steps after `after` until it ends. Returns the function that stops following.
 */
export function followRun(
  id: string,
  handlers: FollowHandlers,
  options: { after?: number } = {},
): () => void {
  let last = options.after ?? 0;
  let stopped = false;
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;

  const stop = () => {
    stopped = true;
    source?.close();
    source = undefined;
    if (timer) clearTimeout(timer);
  };

  const connect = () => {
    if (stopped) return;
    const url = `${API_BASE}/agents/runs/${encodeURIComponent(id)}/stream?after=${last}`;
    const current = new EventSource(url);
    source = current;
    current.onopen = () => {
      if (stopped || source !== current) return;
      failures = 0;
      handlers.onConnection("live");
    };
    current.addEventListener("step", (event) => {
      if (stopped || source !== current) return;
      let parsed: ReturnType<typeof stepSchema.safeParse>;
      try {
        parsed = stepSchema.safeParse(
          JSON.parse((event as MessageEvent<string>).data),
        );
      } catch {
        return;
      }
      if (!parsed.success || parsed.data.seq <= last) return; // never twice
      last = parsed.data.seq;
      handlers.onStep(parsed.data);
    });
    current.addEventListener("partial", (event) => {
      if (stopped || source !== current || !handlers.onPartial) return;
      let parsed: ReturnType<typeof partialSchema.safeParse>;
      try {
        parsed = partialSchema.safeParse(
          JSON.parse((event as MessageEvent<string>).data),
        );
      } catch {
        return;
      }
      if (parsed.success) handlers.onPartial(parsed.data);
    });
    current.addEventListener("end", () => {
      if (stopped || source !== current) return;
      stop();
      handlers.onConnection("ended");
    });
    current.addEventListener("gone", () => {
      if (stopped || source !== current) return;
      stop();
      handlers.onConnection("ended");
      handlers.onGone?.();
    });
    current.onerror = () => {
      if (stopped || source !== current) return;
      handlers.onConnection("reconnecting");
      // CONNECTING: the browser retries by itself and sends Last-Event-ID. CLOSED: it gave up
      // (the server answered an error, a proxy cut in): reconnect ourselves from `last`.
      if (current.readyState === EventSource.CLOSED) {
        current.close();
        source = undefined;
        failures += 1;
        const wait = Math.min(
          followTiming.maxRetryMs,
          followTiming.retryMs * 2 ** Math.min(failures - 1, 4),
        );
        timer = setTimeout(retry, wait);
      }
    };
  };

  // Before reconnecting by hand, ask whether the run is still there: a 404 (deleted, or the
  // session now belongs to someone else) ends the follow instead of retrying for ever. A 401
  // reaches the app's sign out listener through `request`.
  const retry = () => {
    if (stopped) return;
    getRun(id).then(connect, (error: unknown) => {
      if (stopped) return;
      if (isApiError(error) && error.status === 404) {
        stop();
        handlers.onConnection("ended");
        handlers.onGone?.();
        return;
      }
      connect();
    });
  };

  handlers.onConnection("connecting");
  connect();
  return stop;
}
