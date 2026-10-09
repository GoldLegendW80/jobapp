/**
 * Calls of /api/jobs (SPEC-jobs.md, api/app/jobs/router.py). Bodies are checked with zod.
 * Status and contract type are read as plain strings: a value a newer api adds still renders
 * (with a fallback label) instead of failing the whole page.
 */
import { z } from "zod";

import { API_BASE, request } from "@/lib/api";

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
