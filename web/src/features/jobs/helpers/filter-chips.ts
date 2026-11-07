/** The active filters as chips: what each says, and the filters once it is removed. Pure. */
import type { JobFilters } from "./filters";
import { formatDate } from "./format";
import { STATUS_LABELS } from "./labels";

export interface FilterChip {
  key: string;
  text: string;
  /** The filters without this one. */
  without: JobFilters;
}

/** One chip per status and per type, one for the search, the company, the fit range and the
 * found dates each, in that order. */
export function activeFilterChips(filters: JobFilters): FilterChip[] {
  const chips: FilterChip[] = [];
  if (filters.q)
    chips.push({
      key: "q",
      text: `Search: ${filters.q}`,
      without: { ...filters, q: "" },
    });
  for (const status of filters.statuses)
    chips.push({
      key: `status-${status}`,
      text: `Status: ${STATUS_LABELS[status]}`,
      without: {
        ...filters,
        statuses: filters.statuses.filter((s) => s !== status),
      },
    });
  for (const type of filters.types)
    chips.push({
      key: `type-${type}`,
      text: `Type: ${type}`,
      without: { ...filters, types: filters.types.filter((t) => t !== type) },
    });
  if (filters.company)
    chips.push({
      key: "company",
      text: `Company: ${filters.company}`,
      without: { ...filters, company: "" },
    });
  if (filters.fitMin !== null || filters.fitMax !== null)
    chips.push({
      key: "fit",
      text: `Fit: ${filters.fitMin ?? 0} to ${filters.fitMax ?? 100}`,
      without: { ...filters, fitMin: null, fitMax: null },
    });
  if (filters.foundFrom || filters.foundTo)
    chips.push({
      key: "found",
      text:
        filters.foundFrom && filters.foundTo
          ? `Found: ${formatDate(filters.foundFrom)} to ${formatDate(filters.foundTo)}`
          : filters.foundFrom
            ? `Found since ${formatDate(filters.foundFrom)}`
            : `Found until ${formatDate(filters.foundTo)}`,
      without: { ...filters, foundFrom: null, foundTo: null },
    });
  return chips;
}
