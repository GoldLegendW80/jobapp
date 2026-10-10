/** GET /api/jobs/places (api/app/jobs/places.py): the map of the filtered jobs. */
import { z } from "zod";

import { request } from "@/lib/api";

import { jobsKey } from "../api";

export const placeSchema = z.object({
  name: z.string(),
  kind: z.string(),
  lat: z.number(),
  lon: z.number(),
  count: z.number().int().nonnegative(),
  counts_by_status: z.record(z.string(), z.number().int().nonnegative()),
  job_ids: z.array(z.string()),
  job_ids_truncated: z.boolean(),
});
export type Place = z.infer<typeof placeSchema>;

export const mapJobSchema = z.object({
  title: z.string(),
  company: z.string(),
  location: z.string(),
  status: z.string(),
  fit: z.number().int().nullable(),
});
export type MapJob = z.infer<typeof mapJobSchema>;

export const placesSchema = z.object({
  places: z.array(placeSchema),
  jobs: z.record(z.string(), mapJobSchema),
  // The most frequent unplaced locations, at most 200 (api UNPLACED_MAX).
  unplaced: z.array(
    z.object({ location: z.string(), count: z.number().int().nonnegative() }),
  ),
  // Distinct unplaced locations, listed or not.
  unplaced_locations: z.number().int().nonnegative(),
  total: z.number().int().nonnegative(),
  placed: z.number().int().nonnegative(),
  unplaced_total: z.number().int().nonnegative(),
});
export type PlacesMap = z.infer<typeof placesSchema>;

export const placesKey = (query: string) =>
  [...jobsKey, "places", query] as const;

/** `query` is the list's filter query (sort, limit and cursor are ignored by the api). */
export function getPlaces(
  query: string,
  signal?: AbortSignal,
): Promise<PlacesMap> {
  return request(`/jobs/places${query ? `?${query}` : ""}`, {
    signal,
    timeoutMs: 15_000,
    validate: (data) => placesSchema.parse(data),
  });
}
