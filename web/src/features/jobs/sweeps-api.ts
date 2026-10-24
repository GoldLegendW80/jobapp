/**
 * Calls of /api/jobs/sweeps (api/app/jobs/sweep_router.py) and the live view of one sweep.
 *
 * `watchSweep` follows a running sweep through its server-sent event stream and falls back to
 * polling GET /api/jobs/sweeps/{id} when the stream fails (a proxy that buffers, a browser
 * without EventSource), so progress always arrives one way or the other.
 */
import { z } from "zod";

import { API_BASE, isApiError, request } from "@/lib/api";

import type { SweepSchedule } from "./schedule";

export const boardFailureSchema = z.object({
  company: z.string(),
  ats: z.string(),
  org: z.string(),
  error: z.string(),
  message: z.string(),
});
export type BoardFailure = z.infer<typeof boardFailureSchema>;

/** One source of the sweep (api/app/jobs/schemas.py SourceOut). */
export const sweepSourceSchema = z.object({
  source_id: z.string().nullable().optional(),
  mode: z.string().nullable().optional(),
  phase: z.string().nullable().optional(),
  outcome: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
  kind: z.string(),
  query: z.string(),
  location: z.string().nullable(),
  result_pages: z.number().int().nonnegative(),
  found: z.number().int().nonnegative(),
  new: z.number().int().nonnegative(),
  // Opportunities O8: what the never apply rules set aside; absent from an older api.
  skipped_by_rule: z.number().int().nonnegative().default(0),
  error: z.string().nullable(),
});
export type SweepSource = z.infer<typeof sweepSourceSchema>;

export const sweepSchema = z.object({
  id: z.string().min(1),
  trigger: z.string(),
  status: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  error: z.string().nullable(),
  boards_total: z.number().int().nonnegative(),
  boards_done: z.number().int().nonnegative(),
  boards_ok: z.number().int().nonnegative(),
  boards_failed: z.array(boardFailureSchema),
  current_board: z.string().nullable(),
  seen: z.number().int().nonnegative(),
  matched: z.number().int().nonnegative(),
  new: z.number().int().nonnegative(),
  duplicates: z.number().int().nonnegative(),
  skipped_by_rule: z.number().int().nonnegative(),
  duration: z.number().nullable(),
  // Absent from an api older than browser B8.
  sources: z.array(sweepSourceSchema).default([]),
});
export type Sweep = z.infer<typeof sweepSchema>;

export const sweepJobSchema = z.object({
  id: z.string().min(1),
  company: z.string(),
  title: z.string(),
  location: z.string(),
  fit: z.number().int().nullable(),
  status: z.string(),
});
export type SweepJob = z.infer<typeof sweepJobSchema>;

export const sweepDetailSchema = sweepSchema.extend({
  new_jobs: z.array(sweepJobSchema),
});
export type SweepDetail = z.infer<typeof sweepDetailSchema>;

export const scheduleSchema = z.object({
  enabled: z.boolean(),
  days: z.array(z.string()),
  times: z.array(z.string()),
  timezone: z.string(),
  /** Off, or on with no enabled board: nothing runs. */
  active: z.boolean(),
  next_run: z.string().nullable(),
});
export type ScheduleState = z.infer<typeof scheduleSchema>;

export const sweepListSchema = z.object({
  items: z.array(sweepSchema),
  schedule: scheduleSchema,
});
export type SweepList = z.infer<typeof sweepListSchema>;

export const sweepsKey = ["jobs", "sweeps"] as const;

export function startSweep(): Promise<Sweep> {
  return request("/jobs/sweeps", {
    method: "POST",
    timeoutMs: 15_000,
    validate: (data) => sweepSchema.parse(data),
  });
}

/** The id of the sweep already running, when `error` is the 409 of a second start. */
export function runningSweepOf(error: unknown): string | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const body = error.body as { sweep_id?: unknown } | null;
  return typeof body?.sweep_id === "string" ? body.sweep_id : undefined;
}

export function listSweeps(signal?: AbortSignal): Promise<SweepList> {
  return request("/jobs/sweeps?limit=20", {
    signal,
    timeoutMs: 15_000,
    validate: (data) => sweepListSchema.parse(data),
  });
}

export function getSweep(
  id: string,
  signal?: AbortSignal,
): Promise<SweepDetail> {
  return request(`/jobs/sweeps/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => sweepDetailSchema.parse(data),
  });
}

/** How often a sweep is polled when its event stream is not available (tests shorten it). */
export const sweepTiming = { pollMs: 1500 };

export interface WatchHandlers {
  onProgress: (sweep: Sweep) => void;
  onDone: (sweep: SweepDetail) => void;
  onError: (error: unknown) => void;
}

/**
 * Follow one sweep until it ends. Returns the function that stops following it.
 * `useEventSource: false` polls only (tests, and browsers without EventSource).
 */
export function watchSweep(
  id: string,
  handlers: WatchHandlers,
  options: { useEventSource?: boolean; pollMs?: number } = {},
): () => void {
  let stopped = false;
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const controller = new AbortController();
  const pollMs = options.pollMs ?? sweepTiming.pollMs;

  const stop = () => {
    stopped = true;
    source?.close();
    if (timer) clearTimeout(timer);
    controller.abort();
  };

  const poll = () => {
    if (stopped) return;
    getSweep(id, controller.signal).then(
      (sweep) => {
        if (stopped) return;
        if (sweep.status === "running") {
          handlers.onProgress(sweep);
          timer = setTimeout(poll, pollMs);
        } else {
          stop();
          handlers.onDone(sweep);
        }
      },
      (error: unknown) => {
        if (stopped) return;
        if (isApiError(error) && error.kind === "aborted") return;
        if (isApiError(error) && error.status === 404) {
          stop();
          handlers.onError(error);
          return;
        }
        // A blip (network, timeout, 5xx): try again a little later.
        timer = setTimeout(poll, pollMs * 2);
      },
    );
  };

  const useEventSource =
    (options.useEventSource ?? true) && typeof EventSource !== "undefined";
  if (!useEventSource) {
    poll();
    return stop;
  }

  source = new EventSource(
    `${API_BASE}/jobs/sweeps/${encodeURIComponent(id)}/events`,
  );
  source.addEventListener("progress", (event) => {
    const parsed = sweepSchema.safeParse(
      JSON.parse((event as MessageEvent<string>).data),
    );
    if (parsed.success && !stopped) handlers.onProgress(parsed.data);
  });
  source.addEventListener("done", (event) => {
    const parsed = sweepDetailSchema.safeParse(
      JSON.parse((event as MessageEvent<string>).data),
    );
    if (stopped) return;
    if (parsed.success) {
      stop();
      handlers.onDone(parsed.data);
    } else {
      source?.close();
      poll();
    }
  });
  source.addEventListener("gone", () => {
    if (stopped) return;
    stop();
    handlers.onError(new Error("The sweep no longer exists."));
  });
  source.onerror = () => {
    // The stream broke (or a proxy refused it): poll from now on.
    if (stopped) return;
    source?.close();
    source = undefined;
    poll();
  };
  return stop;
}

/* --- the schedule, saved in the profile's search section ----------------------------------- */

const profileSchema = z.object({
  revision: z.number().int().nonnegative(),
  sections: z.record(z.string(), z.record(z.string(), z.unknown()).nullable()),
});
type ProfileBody = z.infer<typeof profileSchema>;

/** Every query of the profile page, refreshed after the schedule changes. */
export const profileQueryKey = ["profile"] as const;

function getProfile(): Promise<ProfileBody> {
  return request("/profile", {
    timeoutMs: 15_000,
    validate: (data) => profileSchema.parse(data),
  });
}

function putSearch(
  revision: number,
  search: Record<string, unknown>,
): Promise<ProfileBody> {
  return request("/profile/search", {
    method: "PUT",
    body: { base_revision: revision, data: search },
    timeoutMs: 15_000,
    validate: (data) => profileSchema.parse(data),
  });
}

/**
 * Save `schedule` as `search.sweep_schedule`, keeping every other field of the section as the
 * server has it. When the profile changed meanwhile (409), the schedule is put onto the newer
 * section the answer carries and sent once more; a second conflict is reported.
 */
export async function saveSchedule(schedule: SweepSchedule): Promise<void> {
  let profile = await getProfile();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const search = profile.sections.search ?? {};
    try {
      await putSearch(profile.revision, {
        ...search,
        sweep_schedule: schedule,
      });
      return;
    } catch (error) {
      if (attempt === 0 && isApiError(error) && error.status === 409) {
        const newer = profileSchema.safeParse(error.body);
        profile = newer.success ? newer.data : await getProfile();
        continue;
      }
      throw error;
    }
  }
}
