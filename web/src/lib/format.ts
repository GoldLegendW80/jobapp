/**
 * Dates and counts as every page shows them, in one format across the app: "3 Nov 2026",
 * "3 Nov 2026, 08:00" and "14,115". Pages import from here instead of building their own
 * Intl formats, so the same moment never reads two ways on two pages.
 */
const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const COUNT = new Intl.NumberFormat("en-GB");

/** "9 Oct 2026" from an ISO date or timestamp; the raw text if it does not parse. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(
    /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso,
  );
  return Number.isNaN(date.getTime()) ? iso : DATE.format(date);
}

/** "9 Oct 2026, 08:00" in the browser's time zone; the raw text if it does not parse. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date);
}

/** A count with thousands separators: 14115 reads "14,115". */
export function formatCount(value: number): string {
  return COUNT.format(value);
}
