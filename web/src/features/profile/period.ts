/**
 * The start and the end of an employer and of a school are a period: a month, "2021-03", or a
 * year alone, "2021", when that is all the user knows. The value is kept as written (spaces around it removed):
 * nothing here pads a year into a month or shortens a month to a year.
 *
 * Twin of the period helpers of api/app/profile/schemas.py (parse_period, period_key,
 * period_span, format_period): the same form, order, comparison and words, so the browser and the
 * api agree. The api stays the judge of a save.
 *
 * - `periodKey`: the order of a list. [year, month], month 0 for a year alone, so a year alone
 *   comes before every month of its year and "newest first" lists it after them. Two different
 *   periods never share a key here, where a year is ASCII digits only (the api also takes the
 *   digits of other scripts, as it always did), so a sort by it is stable and defined.
 * - `periodSpan`: the first and the last month a period can mean. `endsBeforeStart` reads the
 *   start at its first month and the end at its last, and so refuses only what is certain:
 *   "2023-05" to "2023" and "2021" to "2021-03" are in order, "2023" to "2022-12" is not.
 * - `formatPeriod`: the words shown to a person, "2021" or "March 2021". A year alone is shown as
 *   the year: no month is ever made up for it.
 *
 * Every function takes text as typed and gives null (or false) for anything that is not a period.
 */

export const PERIOD_MIN_YEAR = 1900;
export const PERIOD_MAX_YEAR = 2100;
export const PERIOD_PATTERN = /^(\d{4})(?:-(0[1-9]|1[0-2]))?$/;

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

export interface Period {
  year: number;
  /** 1 to 12, or null for a year alone */
  month: number | null;
}

/** The year and month of YYYY or YYYY-MM between 1900 and 2100, or null. */
export function parsePeriod(value: string): Period | null {
  const match = PERIOD_PATTERN.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  if (year < PERIOD_MIN_YEAR || year > PERIOD_MAX_YEAR) return null;
  return { year, month: match[2] === undefined ? null : Number(match[2]) };
}

export const isPeriod = (value: string): boolean => parsePeriod(value) !== null;

/** The sort key of a period: [year, month], month 0 for a year alone. */
export function periodKey(value: string): [number, number] | null {
  const period = parsePeriod(value);
  return period && [period.year, period.month ?? 0];
}

/**
 * The first and the last month a period can mean, each counted in months (year x 12 + month,
 * January being 0): equal for a month, January and December for a year alone.
 */
export function periodSpan(value: string): [number, number] | null {
  const period = parsePeriod(value);
  if (!period) return null;
  const january = period.year * 12;
  return period.month === null
    ? [january, january + 11]
    : [january + period.month - 1, january + period.month - 1];
}

/** True when both are periods and the end is certainly before the start. */
export function endsBeforeStart(start: string, end: string): boolean {
  const [from, to] = [periodSpan(start), periodSpan(end)];
  return from !== null && to !== null && to[1] < from[0];
}

/** A period in words: "2021" for a year alone, "March 2021" for a month; null otherwise. */
export function formatPeriod(value: string): string | null {
  const period = parsePeriod(value);
  if (!period) return null;
  return period.month === null
    ? String(period.year)
    : `${MONTH_NAMES[period.month - 1] ?? ""} ${String(period.year)}`;
}

/**
 * The line under a date field: how the app reads what is typed, or the two forms it takes while
 * the text is not one of them yet.
 */
export function describePeriod(typed: string): string {
  const period = parsePeriod(typed.trim());
  if (!period) return "A year alone or a month: 2021 or 2021-03.";
  const words = formatPeriod(typed.trim()) ?? "";
  return period.month === null
    ? `Read as the year ${words}.`
    : `Read as ${words}.`;
}

/**
 * What a date field holds after a keystroke: a fifth digit typed straight after the year gets the
 * hyphen of YYYY-MM put before it ("20210" becomes "2021-0"), and so do six digits pasted at
 * once. The field asks a phone for its number pad (inputMode numeric), and the digits only pad
 * of an iPhone has no hyphen key. Only when text was added at the end, so the hyphen can still
 * be deleted. It writes down what was typed, nothing more: four digits stay a year alone.
 */
export function typedPeriod(previous: string, typed: string): string {
  const added = typed.length > previous.length && typed.startsWith(previous);
  return added && /^\d{5,6}$/.test(typed)
    ? `${typed.slice(0, 4)}-${typed.slice(4)}`
    : typed;
}
