/**
 * Calls of /api/jobs/searches (api/app/jobs/searches_router.py, jobs J32): the person's searches,
 * the main one first (`id` "main", the profile's search section, changed in Settings and Find
 * jobs through PUT /api/profile/search) and the saved ones, and their runs and the line.
 */
import { z } from "zod";

import type { Search } from "@/features/profile/api/profile-api";
import { request } from "@/lib/api";

import { scheduleSchema, sweepSchema } from "./sweeps-api";

export const MAIN_SEARCH = "main";

/** The keys of one search (searches.CRITERIA): the only ones a saved search holds. */
export const CRITERIA = [
  "title_include",
  "title_exclude",
  "location",
  "location_exclude",
  "remote_stance",
  "contract_types",
  "days",
  "interactive_sites",
  "company_boards",
  "job_feeds",
  "web_search",
  "interactive_instructions",
] as const;
export type Criteria = Partial<Pick<Search, (typeof CRITERIA)[number]>>;

export const searchRequestSchema = z.object({
  id: z.string().min(1),
  search_id: z.string(),
  /** 1 starts next. */
  place: z.number().int().positive(),
  trigger: z.string(),
  requested_at: z.string(),
});
export type SearchRequest = z.infer<typeof searchRequestSchema>;

/** A search's newest sweep, however old (jobs J33b); null for a search that never ran. */
export const lastRunSchema = z.object({
  sweep_id: z.string().min(1),
  status: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  new: z.number().int().nonnegative(),
});
export type LastRun = z.infer<typeof lastRunSchema>;

export const savedSearchSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  main: z.boolean(),
  revision: z.number().int(),
  criteria: z
    .record(z.string(), z.unknown())
    .transform((value) => value as Criteria),
  schedule: scheduleSchema,
  request: searchRequestSchema.nullable().default(null),
  last_run: lastRunSchema.nullable().default(null),
});
export type SavedSearch = z.infer<typeof savedSearchSchema>;

const searchListSchema = z.object({
  items: z.array(savedSearchSchema),
  running_sweep_id: z.string().nullable().default(null),
});
export type SearchList = z.infer<typeof searchListSchema>;

const runSchema = z.object({
  search_id: z.string(),
  outcome: z.string(),
  sweep: sweepSchema.nullable().default(null),
  request: searchRequestSchema.nullable().default(null),
});
export type SearchRun = z.infer<typeof runSchema>;

export const searchesKey = ["jobs", "searches"] as const;

/** The saved schedule of a search: days and times; the time zone is the person's. */
export interface SavedSchedule {
  enabled: boolean;
  days: string[];
  times: string[];
}

export interface SavedSearchBody {
  name: string;
  criteria: Criteria;
  schedule: SavedSchedule;
}

export function listSearches(signal?: AbortSignal): Promise<SearchList> {
  return request("/jobs/searches", {
    signal,
    timeoutMs: 15_000,
    validate: (data) => searchListSchema.parse(data),
  });
}

export function createSearch(body: SavedSearchBody): Promise<SavedSearch> {
  return request("/jobs/searches", {
    method: "POST",
    body,
    timeoutMs: 15_000,
    validate: (data) => savedSearchSchema.parse(data),
  });
}

/** The whole saved search again, on the revision it was read at (409 when it moved). */
export function changeSearch(
  id: string,
  revision: number,
  body: SavedSearchBody,
): Promise<SavedSearch> {
  return request(`/jobs/searches/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { ...body, revision },
    timeoutMs: 15_000,
    validate: (data) => savedSearchSchema.parse(data),
  });
}

export async function deleteSearch(id: string): Promise<void> {
  await request(`/jobs/searches/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** Run now: `started` with the sweep, or `queued` with its place in the line. */
export function runSearch(id: string): Promise<SearchRun> {
  return request(`/jobs/searches/${encodeURIComponent(id)}/run`, {
    method: "POST",
    timeoutMs: 15_000,
    validate: (data) => runSchema.parse(data),
  });
}

export function runAllSearches(): Promise<SearchRun[]> {
  return request("/jobs/searches/run", {
    method: "POST",
    timeoutMs: 30_000,
    validate: (data) =>
      z.object({ items: z.array(runSchema) }).parse(data).items,
  });
}

/** Takes back the search's run waiting in the line. */
export async function takeBackRun(id: string): Promise<void> {
  await request(`/jobs/searches/${encodeURIComponent(id)}/request`, {
    method: "DELETE",
  });
}

/** The body of a saved search, from what the list served. */
export function bodyOf(search: SavedSearch): SavedSearchBody {
  return {
    name: search.name,
    criteria: search.criteria,
    schedule: {
      enabled: search.schedule.enabled,
      days: [...search.schedule.days],
      times: [...search.schedule.times],
    },
  };
}
