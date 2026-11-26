/** The active filters as chips: what each says, and the filters once it is removed. Pure. */
import { formatCount } from "@/lib/format";
import { PAY_PERIOD_WORDS } from "@/lib/wording";

import { CRITERIA_CHOICE_LABELS } from "./criteria-words";
import type { JobFilters } from "./filters";
import { formatDate } from "./format";
import {
  APPLICATION_CHOICE_LABELS,
  STATUS_LABELS,
  WORK_MODE_CHOICE_LABELS,
  websiteName,
  type ApplicationChoice,
  type JobStatus,
  type WorkModeChoice,
} from "./labels";

export interface FilterChip {
  key: string;
  text: string;
  /** The filters without this one. */
  without: JobFilters;
}

/** One chip per value of a repeated filter. */
function each(
  filters: JobFilters,
  field:
    "statuses" | "types" | "places" | "workModes" | "sources" | "applications",
  label: (value: string) => string,
): FilterChip[] {
  const values: readonly string[] = filters[field];
  return values.map((value) => ({
    key: `${field}-${value}`,
    text: label(value),
    without: { ...filters, [field]: values.filter((v) => v !== value) },
  }));
}

function publishedText(filters: JobFilters): string {
  const within = filters.publishedWithin;
  const since =
    within === null
      ? `Published since ${formatDate(filters.publishedFrom)}`
      : within === 0
        ? "Published today"
        : `Published in the last ${within === 1 ? "day" : `${within} days`}`;
  const both =
    within !== null && filters.publishedFrom
      ? `${since}, since ${formatDate(filters.publishedFrom)}`
      : since;
  return filters.publishedUndated ? `${both}, or no date` : both;
}

function payText(filters: JobFilters): string {
  if (filters.payMin !== null && filters.payPeriod !== null)
    return `Pay: at least ${formatCount(filters.payMin)} ${PAY_PERIOD_WORDS[filters.payPeriod] ?? ""}`.trim();
  return filters.pay === "not_stated" ? "Pay: not stated" : "Pay: stated";
}

/** The chips in the order of the Filters panel: the search first, then place, work mode,
 * contract, criteria, website, published, pay, fit, application, status, found, company, and the
 * search that added the postings. */
export function activeFilterChips(filters: JobFilters): FilterChip[] {
  const chips: FilterChip[] = [];
  if (filters.q)
    chips.push({
      key: "q",
      text: `Search: ${filters.q}`,
      without: { ...filters, q: "" },
    });
  chips.push(
    ...each(filters, "places", (place) => `Place: ${place}`),
    ...each(
      filters,
      "workModes",
      (mode) => `Work mode: ${WORK_MODE_CHOICE_LABELS[mode as WorkModeChoice]}`,
    ),
    ...each(filters, "types", (type) => `Contract: ${type}`),
  );
  if (filters.criteria)
    chips.push({
      key: "criteria",
      text: CRITERIA_CHOICE_LABELS[filters.criteria],
      without: { ...filters, criteria: null },
    });
  chips.push(
    ...each(filters, "sources", (source) => `Website: ${websiteName(source)}`),
  );
  if (filters.publishedWithin !== null || filters.publishedFrom)
    chips.push({
      key: "published",
      text: publishedText(filters),
      without: {
        ...filters,
        publishedWithin: null,
        publishedFrom: null,
        publishedUndated: false,
      },
    });
  if (filters.pay !== null || filters.payMin !== null)
    chips.push({
      key: "pay",
      text: payText(filters),
      without: { ...filters, pay: null, payMin: null, payPeriod: null },
    });
  if (filters.fitMin !== null || filters.fitMax !== null)
    chips.push({
      key: "fit",
      text: `Fit: ${filters.fitMin ?? 0} to ${filters.fitMax ?? 100}`,
      without: { ...filters, fitMin: null, fitMax: null },
    });
  chips.push(
    ...each(
      filters,
      "applications",
      (choice) =>
        `Application: ${APPLICATION_CHOICE_LABELS[choice as ApplicationChoice]}`,
    ),
    ...each(
      filters,
      "statuses",
      (status) => `Status: ${STATUS_LABELS[status as JobStatus]}`,
    ),
  );
  if (filters.foundFrom || filters.foundTo)
    chips.push({
      key: "found",
      text:
        filters.foundFrom && filters.foundTo
          ? filters.foundFrom === filters.foundTo
            ? `Found on ${formatDate(filters.foundFrom)}`
            : `Found: ${formatDate(filters.foundFrom)} to ${formatDate(filters.foundTo)}`
          : filters.foundFrom
            ? `Found since ${formatDate(filters.foundFrom)}`
            : `Found until ${formatDate(filters.foundTo)}`,
      without: { ...filters, foundFrom: null, foundTo: null },
    });
  if (filters.company)
    chips.push({
      key: "company",
      text: `Company: ${filters.company}`,
      without: { ...filters, company: "" },
    });
  if (filters.sweep)
    chips.push({
      key: "sweep",
      text: "Added by one search",
      without: { ...filters, sweep: null },
    });
  return chips;
}
