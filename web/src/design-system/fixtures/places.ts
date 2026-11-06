import { http, HttpResponse } from "msw";

import type { JobListItem } from "@/features/jobs/api/jobs-api";
import type { Place, PlacesMap } from "@/features/jobs/api/map/places-api";
import { server } from "@/test/server";

import { POSTINGS } from "./postings";

/** Where the invented postings' cities are. */
const CITIES: Record<string, { lat: number; lon: number }> = {
  Lyon: { lat: 45.76, lon: 4.84 },
  Paris: { lat: 48.86, lon: 2.35 },
  Grenoble: { lat: 45.19, lon: 5.72 },
  Bordeaux: { lat: 44.84, lon: -0.58 },
  Nantes: { lat: 47.22, lon: -1.55 },
  Lille: { lat: 50.63, lon: 3.06 },
  Villeurbanne: { lat: 45.77, lon: 4.88 },
  Rennes: { lat: 48.11, lon: -1.68 },
};

/**
 * GET /api/jobs/places as the api builds it from `rows`: a pin per city a location starts with,
 * the rest (a remote posting) unplaced.
 */
export function placesOf(rows: JobListItem[]): PlacesMap {
  const places = new Map<string, Place>();
  const unplaced = new Map<string, number>();
  for (const row of rows) {
    const city = row.location.split(",")[0]?.trim() ?? "";
    const at = CITIES[city];
    if (!at) {
      unplaced.set(row.location, (unplaced.get(row.location) ?? 0) + 1);
      continue;
    }
    const place = places.get(city) ?? {
      name: city,
      kind: "city",
      ...at,
      count: 0,
      counts_by_status: {},
      job_ids: [],
      job_ids_truncated: false,
    };
    place.count += 1;
    place.counts_by_status[row.status] =
      (place.counts_by_status[row.status] ?? 0) + 1;
    place.job_ids.push(row.id);
    places.set(city, place);
  }
  const placed = [...places.values()].reduce((sum, p) => sum + p.count, 0);
  return {
    places: [...places.values()].sort((a, b) => b.count - a.count),
    jobs: Object.fromEntries(
      rows.map((row) => [
        row.id,
        {
          title: row.title,
          company: row.company,
          location: row.location,
          status: row.status,
          fit: row.fit,
        },
      ]),
    ),
    unplaced: [...unplaced].map(([location, count]) => ({ location, count })),
    unplaced_locations: unplaced.size,
    total: rows.length,
    placed,
    unplaced_total: rows.length - placed,
  };
}

/** GET /api/jobs/places answers the map of `rows`, or `respond`'s answer instead. */
export function fakePlaces(
  rows: JobListItem[] = POSTINGS,
  respond?: () => Response,
) {
  server.use(
    http.get("*/api/jobs/places", () =>
      respond ? respond() : HttpResponse.json(placesOf(rows)),
    ),
  );
}
