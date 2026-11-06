import { STATUS_LABELS, STATUSES, type JobStatus } from "../labels";
import type { Place } from "@/features/jobs/api/map/places-api";

/** Pin colours by the dominant status of a place, readable on the land in light and dark. */
export const STATUS_FILL: Record<JobStatus, string> = {
  found: "bg-muted-foreground",
  shortlisted: "bg-info",
  // The highlighter is too pale for the light land on its own: the edge keeps the pin visible.
  cv_ready: "border border-highlight-foreground/40 bg-highlight",
  staged: "bg-warning",
  applied: "bg-success/60",
  interview: "bg-success",
  rejected: "bg-destructive",
  closed: "bg-muted-foreground/50",
  skipped: "bg-muted-foreground/30",
};

export const plural = (n: number, word: string) =>
  `${n} ${word}${n === 1 ? "" : "s"}`;

/** "3 postings, 2 Found, 1 Applied", statuses in pipeline order. */
export function describePlace(
  place: Pick<Place, "count" | "counts_by_status">,
): string {
  const parts = STATUSES.filter(
    (s) => (place.counts_by_status[s] ?? 0) > 0,
  ).map((s) => `${place.counts_by_status[s]} ${STATUS_LABELS[s]}`);
  return [plural(place.count, "posting"), ...parts].join(", ");
}
