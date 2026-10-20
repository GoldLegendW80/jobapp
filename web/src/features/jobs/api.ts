/**
 * Calls of /api/jobs (SPEC-jobs.md, api/app/jobs/router.py). Bodies are checked with zod.
 * Status and contract type are read as plain strings: a value a newer api adds still renders
 * (with a fallback label) instead of failing the whole page.
 */
import { z } from "zod";

import { API_BASE, isApiError, request } from "@/lib/api";

import { toSearchParams, type JobFilters } from "./filters";
import { STATUSES } from "./labels";

/** Rows per request: Harald's 433 rows come in five pages. */
export const PAGE_SIZE = 100;

export const jobListItemSchema = z.object({
  id: z.string().min(1),
  slug: z.string(),
  company: z.string(),
  title: z.string(),
  location: z.string(),
  contract_type: z.string(),
  url: z.string().nullable(),
  source: z.string().nullable(),
  status: z.string(),
  fit: z.number().int().nullable(),
  fit_overridden: z.boolean(),
  competencies: z.array(z.string()),
  gaps: z.array(z.string()),
  description: z.string().nullable(),
  published_on: z.string().nullable(),
  found_on: z.string(),
  applied_on: z.string().nullable(),
  notes: z.string(),
  file_count: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type JobListItem = z.infer<typeof jobListItemSchema>;

export const fitBucketSchema = z.object({
  low: z.number().int(),
  high: z.number().int(),
  count: z.number().int().nonnegative(),
});
export type FitBucket = z.infer<typeof fitBucketSchema>;

export const jobListPageSchema = z.object({
  items: z.array(jobListItemSchema),
  next_cursor: z.string().nullable(),
  total: z.number().int().nonnegative(),
  counts_by_status: z.record(z.string(), z.number().int().nonnegative()),
  fit_histogram: z.object({
    buckets: z.array(fitBucketSchema),
    unscored: z.number().int().nonnegative(),
  }),
});
export type JobListPage = z.infer<typeof jobListPageSchema>;

export const jobFileSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  mime: z.string(),
  size: z.number().int().nonnegative(),
  sha256: z.string(),
  origin: z.string(),
  created_at: z.string(),
});
export type JobFile = z.infer<typeof jobFileSchema>;

export const jobEventSchema = z.object({
  id: z.number().int(),
  at: z.string(),
  kind: z.string(),
  data: z.record(z.string(), z.unknown()),
});
export type JobEvent = z.infer<typeof jobEventSchema>;

export const jobDetailSchema = jobListItemSchema.extend({
  posting_text: z.string().nullable(),
  legacy: z.record(z.string(), z.unknown()),
  files: z.array(jobFileSchema),
  events: z.array(jobEventSchema),
  events_total: z.number().int().nonnegative(),
});
export type JobDetail = z.infer<typeof jobDetailSchema>;

export const jobsKey = ["jobs"] as const;
export const jobListKey = (query: string) =>
  [...jobsKey, "list", query] as const;
export const jobKey = (id: string) => [...jobsKey, "detail", id] as const;

/** The api query of a filter set (without paging). */
export function listQuery(filters: JobFilters): string {
  const params = toSearchParams(filters);
  if (!params.has("sort")) params.set("sort", filters.sort);
  return params.toString();
}

export function listJobs(
  query: string,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<JobListPage> {
  const params = new URLSearchParams(query);
  params.set("limit", String(PAGE_SIZE));
  if (cursor) params.set("cursor", cursor);
  return request(`/jobs?${params.toString()}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => jobListPageSchema.parse(data),
  });
}

export function getJob(id: string, signal?: AbortSignal): Promise<JobDetail> {
  return request(`/jobs/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => jobDetailSchema.parse(data),
  });
}

/** Where the browser opens a file: inline for a PDF, a download for anything else. */
export function fileUrl(jobId: string, fileId: string): string {
  return `${API_BASE}/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fileId)}`;
}

export function fetchFileJson(
  jobId: string,
  fileId: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fileId)}`,
    { signal, timeoutMs: 15_000 },
  );
}

/** Every known status with its count (0 when the api leaves one out). */
export function statusCounts(
  counts: Record<string, number> | undefined,
): Array<{ status: (typeof STATUSES)[number]; count: number }> {
  return STATUSES.map((status) => ({ status, count: counts?.[status] ?? 0 }));
}

/* --- editing, bulk, add by hand, rescore (J4) --------------------------------------------- */

/** What PATCH /api/jobs/{id} takes: only the fields sent change. */
export interface JobPatch {
  status?: string;
  notes?: string;
  /** A number sets the fit by hand; null goes back to the computed fit. */
  fit?: number | null;
  company?: string;
  title?: string;
  location?: string;
  contract_type?: string;
  url?: string | null;
  /** The version the change is based on: a newer one on the server answers 409. */
  updated_at?: string;
}

export function patchJob(id: string, body: JobPatch): Promise<JobDetail> {
  return request(`/jobs/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
    timeoutMs: 15_000,
    validate: (data) => jobDetailSchema.parse(data),
  });
}

export interface NewPosting {
  company: string;
  title: string;
  location?: string;
  contract_type?: string;
  url?: string | null;
  posting_text?: string | null;
}

export function addJob(body: NewPosting): Promise<JobDetail> {
  return request("/jobs", {
    method: "POST",
    body,
    timeoutMs: 15_000,
    validate: (data) => jobDetailSchema.parse(data),
  });
}

export const bulkResultSchema = z.object({
  results: z.array(
    z.object({
      id: z.string(),
      changed: z.boolean(),
      status: z.string(),
      previous_status: z.string(),
    }),
  ),
  changed: z.number().int().nonnegative(),
  unchanged: z.number().int().nonnegative(),
  undo_token: z.string().nullable(),
  undo_expires_at: z.string().nullable(),
});
export type BulkResult = z.infer<typeof bulkResultSchema>;

/** The api takes at most this many ids per bulk call. */
export const BULK_MAX = 500;

export function bulkUpdate(
  ids: string[],
  set: { status?: string; notes_append?: string },
): Promise<BulkResult> {
  return request("/jobs/bulk", {
    method: "POST",
    body: { ids, set },
    timeoutMs: 30_000,
    validate: (data) => bulkResultSchema.parse(data),
  });
}

export const undoResultSchema = z.object({
  restored: z.array(z.string()),
  skipped: z.array(z.string()),
});
export type UndoResult = z.infer<typeof undoResultSchema>;

export function undoBulk(token: string): Promise<UndoResult> {
  return request("/jobs/bulk/undo", {
    method: "POST",
    body: { token },
    timeoutMs: 30_000,
    validate: (data) => undoResultSchema.parse(data),
  });
}

export const rescoreResultSchema = z.object({
  scored: z.number().int().nonnegative(),
  changed: z.number().int().nonnegative(),
  unchanged: z.number().int().nonnegative(),
  skipped_manual: z.number().int().nonnegative(),
  skipped_no_text: z.number().int().nonnegative(),
  skipped_busy: z.number().int().nonnegative(),
  remaining: z.number().int().nonnegative(),
  next_after: z.string().nullable(),
});
export type RescoreResult = z.infer<typeof rescoreResultSchema>;

export function rescore(body: {
  ids?: string[];
  overwrite_manual: boolean;
  after?: string;
}): Promise<RescoreResult> {
  return request("/jobs/rescore", {
    method: "POST",
    body,
    timeoutMs: 60_000,
    validate: (data) => rescoreResultSchema.parse(data),
  });
}

/** Totals of a rescore that took several calls (the api stops after a time budget). */
export async function rescoreAll(
  options: { ids?: string[]; overwriteManual: boolean },
  onProgress?: (done: RescoreResult) => void,
): Promise<RescoreResult> {
  const total: RescoreResult = {
    scored: 0,
    changed: 0,
    unchanged: 0,
    skipped_manual: 0,
    skipped_no_text: 0,
    skipped_busy: 0,
    remaining: 0,
    next_after: null,
  };
  let after: string | undefined;
  // Each call makes progress or ends; the cap only guards against a server that never ends.
  for (let call = 0; call < 1000; call += 1) {
    const part = await rescore({
      ...(options.ids ? { ids: options.ids } : {}),
      overwrite_manual: options.overwriteManual,
      ...(after ? { after } : {}),
    });
    total.scored += part.scored;
    total.changed += part.changed;
    total.unchanged += part.unchanged;
    total.skipped_manual += part.skipped_manual;
    total.skipped_no_text += part.skipped_no_text;
    total.skipped_busy += part.skipped_busy;
    total.remaining = part.remaining;
    onProgress?.({ ...total });
    if (!part.next_after || part.remaining === 0) break;
    after = part.next_after;
  }
  total.next_after = null;
  return total;
}

/** The existing job of a 409 duplicate answer, when the error is one. */
export function duplicateOf(
  error: unknown,
): { id: string; slug: string } | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const body = error.body as {
    existing_id?: unknown;
    existing_slug?: unknown;
  } | null;
  if (typeof body?.existing_id !== "string") return undefined;
  return {
    id: body.existing_id,
    slug: typeof body.existing_slug === "string" ? body.existing_slug : "",
  };
}

/** Whether an error is the 409 of an edit based on an older version of the job. */
export function isStale(error: unknown): boolean {
  if (!isApiError(error) || error.status !== 409) return false;
  const body = error.body as { updated_at?: unknown } | null;
  return typeof body?.updated_at === "string";
}

/**
 * Every id matching a list query, page after page, up to `cap`. Returns the ids and the total
 * matching (which may be above the cap).
 */
export async function collectIds(
  query: string,
  cap: number,
  signal?: AbortSignal,
): Promise<{ ids: string[]; total: number }> {
  const ids: string[] = [];
  let page = await listJobs(query, null, signal);
  for (;;) {
    for (const item of page.items) if (ids.length < cap) ids.push(item.id);
    if (!page.next_cursor || ids.length >= cap) break;
    page = await listJobs(query, page.next_cursor, signal);
  }
  return { ids, total: page.total };
}
