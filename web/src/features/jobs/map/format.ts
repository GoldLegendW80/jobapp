import { STATUS_LABELS, STATUSES, type JobStatus } from "../labels";
import type { Place } from "./places-api";

/** Pin colours by the dominant status of a place, readable on the land in light and dark. */
export const STATUS_FILL: Record<JobStatus, string> = {
  found: "bg-indigo-500 dark:bg-indigo-400",
  shortlisted: "bg-sky-500 dark:bg-sky-400",
  cv_ready: "bg-violet-500 dark:bg-violet-400",
  staged: "bg-amber-500 dark:bg-amber-400",
  applied: "bg-emerald-500 dark:bg-emerald-400",
  interview: "bg-green-700 dark:bg-green-400",
  rejected: "bg-rose-500 dark:bg-rose-400",
  closed: "bg-zinc-500 dark:bg-zinc-400",
  skipped: "bg-stone-400 dark:bg-stone-500",
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
