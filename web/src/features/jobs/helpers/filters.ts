/**
 * The list filters and their URL form. The URL is the single source of truth (shareable, back and
 * forward work), so parsing must survive anything a person can type or paste: an unknown status,
 * a fit of 140, a date like 2026-02-31, min above max. Whatever is invalid is dropped, never sent
 * to the api (which would answer 422).
 *
 * URL parameters mirror the api's: status (repeated), type (repeated), q, company, fit_min,
 * fit_max, found_from, found_to, place, work_mode, source and application (repeated),
 * published_within, published_from, published_undated, pay, pay_min with pay_period, sweep,
 * search (main or a saved search's id), criteria (within or outside; all, the default, is
 * omitted), listing (only or without, the same), and sort (omitted when it is the default).
 */
import { CRITERIA_CHOICES, type CriteriaChoice } from "./criteria-words";
import { LISTING_CHOICES, type ListingChoice } from "./listed-words";
import {
  APPLICATION_CHOICES,
  CONTRACT_TYPES,
  DEFAULT_SORT,
  isContractType,
  isSort,
  isStatus,
  PAY_PERIODS,
  STATUSES,
  WORK_MODE_CHOICES,
  type ApplicationChoice,
  type ContractType,
  type JobSort,
  type JobStatus,
  type PayPeriodChoice,
  type WorkModeChoice,
} from "./labels";

/** The api refuses longer q and company values. */
export const TEXT_MAX = 200;

/** The pause in typing after which the search box and the company field apply. */
export const SEARCH_DEBOUNCE_MS = 300;

/** The api's bounds: places and websites per filter, a place's length, the days and the pay. */
export const VALUES_MAX = 20;
export const PLACE_MAX = 100;
const PUBLISHED_WITHIN_MAX = 3650;
const PAY_MAX = 10_000_000;

export type PayChoice = "stated" | "not_stated";

export interface JobFilters {
  statuses: JobStatus[];
  types: ContractType[];
  q: string;
  company: string;
  fitMin: number | null;
  fitMax: number | null;
  foundFrom: string | null;
  foundTo: string | null;
  places: string[];
  workModes: WorkModeChoice[];
  sources: string[];
  publishedWithin: number | null;
  publishedFrom: string | null;
  /** With a published bound, the postings without a date stay. */
  publishedUndated: boolean;
  pay: PayChoice | null;
  /** With payPeriod: the top of a posting's pay in that period reaches it. */
  payMin: number | null;
  payPeriod: PayPeriodChoice | null;
  applications: ApplicationChoice[];
  /** The postings one search added. */
  sweep: string | null;
  /** Jobs J33b: the postings one of the person's searches added, `main` or a saved search's id. */
  search: string | null;
  /** Jobs J30: the postings within the criteria of their search, or outside them (a badge). */
  criteria: CriteriaChoice | null;
  /** Jobs J31: the postings seen only in a list (their own page not read), or those read. */
  listing: ListingChoice | null;
  sort: JobSort;
}

/** What each part of the filter bar gets: the filters as the URL holds them, and the change. */
export interface FilterProps {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
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
  places: [],
  workModes: [],
  sources: [],
  publishedWithin: null,
  publishedFrom: null,
  publishedUndated: false,
  pay: null,
  payMin: null,
  payPeriod: null,
  applications: [],
  sweep: null,
  search: null,
  criteria: null,
  listing: null,
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

/** A whole number from `min` to `max`, else none. */
function parseWhole(raw: string | null, min: number, max: number) {
  if (raw === null || !/^\d{1,8}$/.test(raw.trim())) return null;
  const value = Number(raw.trim());
  return value >= min && value <= max ? value : null;
}

/** A place the api takes: trimmed, with a letter or a digit, PLACE_MAX characters at most. */
export function cleanPlace(raw: string): string | null {
  const place = raw.trim();
  return place.length <= PLACE_MAX && /[\p{L}\p{N}]/u.test(place)
    ? place
    : null;
}

/** Texts once each, in the order given, VALUES_MAX at most. */
function texts(values: string[], clean: (value: string) => string | null) {
  const kept = values.map(clean).filter((v): v is string => v !== null);
  return [...new Set(kept)].slice(0, VALUES_MAX);
}

const cleanSource = (raw: string) => {
  const source = raw.trim();
  return source && source.length <= TEXT_MAX ? source : null;
};

const isIn =
  <T extends string>(all: readonly T[]) =>
  (value: string): value is T =>
    (all as readonly string[]).includes(value);

const SWEEP_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The filters of jobs J29, read as parseFilters reads the others. */
function parseMore(params: URLSearchParams) {
  const pay = params.get("pay");
  let payMin = parseWhole(params.get("pay_min"), 1, PAY_MAX);
  const period = params.get("pay_period") ?? "";
  let payPeriod = isIn(PAY_PERIODS)(period) ? period : null;
  if (payMin === null || payPeriod === null || pay === "not_stated") {
    payMin = null;
    payPeriod = null;
  }
  const from = params.get("published_from");
  const sweep = params.get("sweep")?.trim() ?? "";
  const search = params.get("search")?.trim() ?? "";
  const criteria = params.get("criteria") ?? "";
  const listing = params.get("listing") ?? "";
  return {
    places: texts(params.getAll("place"), cleanPlace),
    workModes: pick(
      params.getAll("work_mode"),
      WORK_MODE_CHOICES,
      isIn(WORK_MODE_CHOICES),
    ),
    sources: texts(params.getAll("source"), cleanSource),
    publishedWithin: parseWhole(
      params.get("published_within"),
      0,
      PUBLISHED_WITHIN_MAX,
    ),
    publishedFrom: isIsoDate(from) ? from : null,
    publishedUndated: params.get("published_undated") === "true",
    pay: pay === "stated" || pay === "not_stated" ? pay : null,
    payMin,
    payPeriod,
    applications: pick(
      params.getAll("application"),
      APPLICATION_CHOICES,
      isIn(APPLICATION_CHOICES),
    ),
    sweep: SWEEP_ID.test(sweep) ? sweep.toLowerCase() : null,
    search:
      search === "main"
        ? search
        : SWEEP_ID.test(search)
          ? search.toLowerCase()
          : null,
    criteria: isIn(CRITERIA_CHOICES)(criteria) ? criteria : null,
    listing: isIn(LISTING_CHOICES)(listing) ? listing : null,
  } satisfies Partial<JobFilters>;
}

/** Whether a published bound is set, so that keeping the undated postings means something. */
export const hasPublishedBound = (filters: JobFilters) =>
  filters.publishedWithin !== null || filters.publishedFrom !== null;

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
    ...parseMore(params),
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
  for (const place of filters.places) params.append("place", place);
  for (const mode of filters.workModes) params.append("work_mode", mode);
  for (const source of filters.sources) params.append("source", source);
  if (filters.publishedWithin !== null)
    params.set("published_within", String(filters.publishedWithin));
  if (filters.publishedFrom)
    params.set("published_from", filters.publishedFrom);
  if (filters.publishedUndated && hasPublishedBound(filters))
    params.set("published_undated", "true");
  if (filters.pay) params.set("pay", filters.pay);
  if (filters.payMin !== null && filters.payPeriod !== null) {
    params.set("pay_min", String(filters.payMin));
    params.set("pay_period", filters.payPeriod);
  }
  for (const choice of filters.applications)
    params.append("application", choice);
  if (filters.sweep) params.set("sweep", filters.sweep);
  if (filters.search) params.set("search", filters.search);
  if (filters.criteria) params.set("criteria", filters.criteria);
  if (filters.listing) params.set("listing", filters.listing);
  if (withSort && filters.sort !== DEFAULT_SORT)
    params.set("sort", filters.sort);
  return params;
}

/** True when anything narrows the list (the sort does not). */
export function hasFilters(filters: JobFilters): boolean {
  return toSearchParams(filters, { withSort: false }).toString() !== "";
}

/** How many filters narrow the list, counted as the filter bar's chips are: one per status,
 * type, place, work mode, website and application, one for the search, the company, the fit
 * range, the found dates, the published dates, the pay, the run and the search that added them
 * and the criteria and the listing each. */
export function activeFilterCount(filters: JobFilters): number {
  return (
    filters.statuses.length +
    filters.types.length +
    filters.places.length +
    filters.workModes.length +
    filters.sources.length +
    filters.applications.length +
    (filters.q ? 1 : 0) +
    (filters.company ? 1 : 0) +
    (filters.fitMin !== null || filters.fitMax !== null ? 1 : 0) +
    (filters.foundFrom || filters.foundTo ? 1 : 0) +
    (hasPublishedBound(filters) ? 1 : 0) +
    (filters.pay !== null || filters.payMin !== null ? 1 : 0) +
    (filters.sweep ? 1 : 0) +
    (filters.search ? 1 : 0) +
    (filters.criteria ? 1 : 0) +
    (filters.listing ? 1 : 0)
  );
}

/** The day of `now` where the person is, as ISO. */
export function localIsoDate(now: Date): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${String(now.getFullYear())}-${month}-${day}`;
}

/** Preset: what was found on `today` (an ISO date), the rest kept. */
export const foundToday = (filters: JobFilters, today: string): JobFilters => ({
  ...filters,
  foundFrom: today,
  foundTo: today,
});

/** Preset: every status but Skipped and Closed, the rest kept. */
export const hideSkippedAndClosed = (filters: JobFilters): JobFilters => ({
  ...filters,
  statuses: STATUSES.filter((s) => s !== "skipped" && s !== "closed"),
});

/** A fit typed in the fit range: none when empty, "invalid" unless a whole number to 100. */
export function parseFitInput(raw: string): number | null | "invalid" {
  const value = raw.trim();
  if (value === "") return null;
  if (!/^\d{1,3}$/.test(value)) return "invalid";
  const number = Number(value);
  return number <= 100 ? number : "invalid";
}

const MONTHS = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];

/**
 * A date typed in the found range, as ISO: "6 Nov 2026" (as the app shows dates, a month of three
 * letters or more) or "2026-11-06". None when empty, false when it is no calendar date.
 */
export function parseTypedDate(raw: string): string | null | false {
  const text = raw.trim();
  if (text === "") return null;
  if (isIsoDate(text)) return text;
  const match = /^(\d{1,2})\s+([a-z]{3,})\.?\s+(\d{4})$/i.exec(text);
  if (!match) return false;
  const [, day, word, year] = match as unknown as [
    string,
    string,
    string,
    string,
  ];
  const month = MONTHS.findIndex((name) => name.startsWith(word.toLowerCase()));
  if (month < 0) return false;
  const iso = `${year}-${String(month + 1).padStart(2, "0")}-${day.padStart(2, "0")}`;
  return isIsoDate(iso) ? iso : false;
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

/**
 * The posting the Jobs list shows in its detail pane, kept in the URL beside the filters as
 * `job` (so reload, back and a shared link show it again). Not a filter: the api never sees it,
 * and a filter change drops it, so the new view starts on its first posting.
 */
export const SELECTED_PARAM = "job";

/** The selected posting's id, or null when the URL names none (or something that is no id). */
export function parseSelected(params: URLSearchParams): string | null {
  const id = params.get(SELECTED_PARAM)?.trim() ?? "";
  return /^[\w-]{1,64}$/.test(id) ? id : null;
}

/** A list query ("" or "?status=found") with `id` selected: where a posting page goes back to. */
export function withSelected(query: string, id: string): string {
  const params = new URLSearchParams(query);
  params.set(SELECTED_PARAM, id);
  return `?${params.toString()}`;
}
