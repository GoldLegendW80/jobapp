/**
 * The list filters and their URL form. The URL is the single source of truth (shareable, back and
 * forward work), so parsing must survive anything a person can type or paste: an unknown status,
 * a fit of 140, a date like 2026-02-31, min above max. Whatever is invalid is dropped, never sent
 * to the api (which would answer 422).
 *
 * URL parameters mirror the api's: status (repeated), type (repeated), q, company, fit_min,
 * fit_max, found_from, found_to, sort (omitted when it is the default).
 */
import {
  CONTRACT_TYPES,
  DEFAULT_SORT,
  isContractType,
  isSort,
  isStatus,
  STATUSES,
  type ContractType,
  type JobSort,
  type JobStatus,
} from "./labels";

/** The api refuses longer q and company values. */
export const TEXT_MAX = 200;

export interface JobFilters {
  statuses: JobStatus[];
  types: ContractType[];
  q: string;
  company: string;
  fitMin: number | null;
  fitMax: number | null;
  foundFrom: string | null;
  foundTo: string | null;
  sort: JobSort;
}

export const EMPTY_FILTERS: JobFilters = {
  statuses: [],
  types: [],
  q: "",
  company: "",
  fitMin: null,
  fitMax: null,
  foundFrom: null,
  foundTo: null,
  sort: DEFAULT_SORT,
};

function parseFit(raw: string | null): number | null {
  if (raw === null || !/^\d{1,3}$/.test(raw.trim())) return null;
  const value = Number(raw.trim());
  return value >= 0 && value <= 100 ? value : null;
}

/** A real calendar date in ISO form (2026-02-31 is not one). */
export function isIsoDate(raw: string | null | undefined): raw is string {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return false;
  const [y, m, d] = raw.split("-").map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d));
  return (
    date.getUTCFullYear() === y &&
    date.getUTCMonth() === m - 1 &&
    date.getUTCDate() === d
  );
}

function parseText(raw: string | null): string {
  return (raw ?? "").trim().slice(0, TEXT_MAX);
}

/** Valid values of a repeated parameter, once each, in the order of `all`. */
function pick<T extends string>(
  values: string[],
  all: readonly T[],
  valid: (value: string) => value is T,
): T[] {
  const wanted = new Set(values.filter(valid));
  return all.filter((value) => wanted.has(value));
}

export function parseFilters(params: URLSearchParams): JobFilters {
  let fitMin = parseFit(params.get("fit_min"));
  let fitMax = parseFit(params.get("fit_max"));
  if (fitMin !== null && fitMax !== null && fitMin > fitMax) {
    fitMin = null;
    fitMax = null;
  }
  const from = params.get("found_from");
  const to = params.get("found_to");
  let foundFrom = isIsoDate(from) ? from : null;
  let foundTo = isIsoDate(to) ? to : null;
  if (foundFrom !== null && foundTo !== null && foundFrom > foundTo) {
    foundFrom = null;
    foundTo = null;
  }
  const sort = params.get("sort") ?? "";
  return {
    statuses: pick(params.getAll("status"), STATUSES, isStatus),
    types: pick(params.getAll("type"), CONTRACT_TYPES, isContractType),
    q: parseText(params.get("q")),
    company: parseText(params.get("company")),
    fitMin,
    fitMax,
    foundFrom,
    foundTo,
    sort: isSort(sort) ? sort : DEFAULT_SORT,
  };
}

/** The canonical query of a filter set: same filters, same string, in a stable order. */
export function toSearchParams(
  filters: JobFilters,
  options: { withSort?: boolean } = {},
): URLSearchParams {
  const { withSort = true } = options;
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  for (const status of filters.statuses) params.append("status", status);
  for (const type of filters.types) params.append("type", type);
  if (filters.company) params.set("company", filters.company);
  if (filters.fitMin !== null) params.set("fit_min", String(filters.fitMin));
  if (filters.fitMax !== null) params.set("fit_max", String(filters.fitMax));
  if (filters.foundFrom) params.set("found_from", filters.foundFrom);
  if (filters.foundTo) params.set("found_to", filters.foundTo);
  if (withSort && filters.sort !== DEFAULT_SORT)
    params.set("sort", filters.sort);
  return params;
}

/** True when anything narrows the list (the sort does not). */
export function hasFilters(filters: JobFilters): boolean {
  return toSearchParams(filters, { withSort: false }).toString() !== "";
}

/** The status filter after a click on its KPI tile: that status alone, or none if it was. */
export function toggleOnlyStatus(
  filters: JobFilters,
  status: JobStatus,
): JobFilters {
  const only = filters.statuses.length === 1 && filters.statuses[0] === status;
  return { ...filters, statuses: only ? [] : [status] };
}

/** The fit range after a click on a histogram bucket: that bucket, or none if it was. */
export function toggleFitRange(
  filters: JobFilters,
  low: number,
  high: number,
): JobFilters {
  const same = filters.fitMin === low && filters.fitMax === high;
  return { ...filters, fitMin: same ? null : low, fitMax: same ? null : high };
}
