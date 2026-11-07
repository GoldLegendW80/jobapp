import type { FiltersSummary } from "@/components/shared/jobs/filters/filters-toolbar";
import type { FitBucket } from "@/features/jobs/api/jobs-api";
import {
  EMPTY_FILTERS,
  type JobFilters,
} from "@/features/jobs/helpers/filters";
import type { JobStatus } from "@/features/jobs/helpers/labels";

/** A few weeks of searching: most postings found, a handful moving down the pipeline. */
export const STATUS_COUNTS: Record<JobStatus, number> = {
  found: 128,
  shortlisted: 14,
  cv_ready: 3,
  staged: 2,
  applied: 21,
  interview: 2,
  rejected: 9,
  closed: 4,
  skipped: 57,
};

/** The fit of those postings, in the ten ranges of the histogram. */
export const FIT_BUCKETS: FitBucket[] = [
  { low: 0, high: 9, count: 3 },
  { low: 10, high: 19, count: 6 },
  { low: 20, high: 29, count: 11 },
  { low: 30, high: 39, count: 19 },
  { low: 40, high: 49, count: 26 },
  { low: 50, high: 59, count: 31 },
  { low: 60, high: 69, count: 24 },
  { low: 70, high: 79, count: 15 },
  { low: 80, high: 89, count: 7 },
  { low: 90, high: 100, count: 2 },
];

/** Ten empty ranges: nothing scored. */
export const EMPTY_BUCKETS: FitBucket[] = FIT_BUCKETS.map((bucket) => ({
  ...bucket,
  count: 0,
}));

/** What the list api says of the filters above: counts, fit spread, total. */
export const FILTERS_SUMMARY: FiltersSummary = {
  counts_by_status: STATUS_COUNTS,
  fit_histogram: { buckets: FIT_BUCKETS, unscored: 12 },
  total: 240,
};

/** One of each filter set: statuses, a type, words, a company, a fit range and dates. */
export const SOME_FILTERS: JobFilters = {
  ...EMPTY_FILTERS,
  statuses: ["shortlisted", "cv_ready"],
  types: ["CDI", "Freelance"],
  q: "data engineer",
  company: "Lumen Freight",
  fitMin: 70,
  fitMax: 100,
  foundFrom: "2026-10-01",
  foundTo: "2026-11-06",
};
