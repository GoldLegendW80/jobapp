/**
 * What the Full auto pages show, as pure functions: the hold's countdown read on the server's
 * clock, the sentence that says what a Like does once Full auto is on, the rehearsals left and
 * the daily summary in words. No dashes as separators.
 */
import type { Summary } from "./queue-api";
import { days, holdText, type ApplySettings } from "./settings-api";

/**
 * How far the server's clock is ahead of the page's, from the server's `now` and the page's
 * time when that answer arrived. 0 when the server sent no time.
 */
export function serverOffset(
  serverNow: string | undefined,
  receivedAt: number,
): number {
  const at = serverNow ? Date.parse(serverNow) : Number.NaN;
  return Number.isFinite(at) && receivedAt > 0 ? at - receivedAt : 0;
}

/** Milliseconds left in the hold at the page's time `pageNow`, on the server's clock. */
export function holdLeft(
  until: string,
  offset: number,
  pageNow: number,
): number {
  const end = Date.parse(until);
  if (!Number.isFinite(end)) return 0;
  return Math.max(0, end - (pageNow + offset));
}

const two = (n: number) => String(n).padStart(2, "0");

/** "14:59", "1:00:00": the time left, rounded up to the second. */
export function clockText(ms: number): string {
  const total = Math.ceil(Math.max(0, ms) / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${hours}:${two(minutes)}:${two(seconds)}`
    : `${minutes}:${two(seconds)}`;
}

/** "about 15 minutes": what a screen reader hears, once a minute at most. */
export function spokenLeft(ms: number): string {
  const minutes = Math.ceil(Math.max(0, ms) / 60_000);
  if (minutes <= 1) return "less than a minute";
  if (minutes < 60) return `about ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "about 1 hour" : `about ${hours} hours`;
}

/** The sentence of the Full auto card, with the person's own threshold, hold and company days. */
export function fullAutoSentence(settings: ApplySettings): string {
  return (
    `The app will submit applications for you when a posting's fit is at least ${settings.min_fit}, ` +
    `at least ${holdText(settings.hold_s)} after each Like unless you cancel, ` +
    `and never more than one application per company in ${days(settings.company_days)}.`
  );
}

export const rehearsalsLeft = (settings: ApplySettings) =>
  Math.max(0, settings.rehearsals - settings.rehearsals_done);

/** What the rehearsals are, before Full auto is on and while some are left. */
export function rehearsalsSentence(settings: ApplySettings): string {
  const left = rehearsalsLeft(settings);
  if (left === 0)
    return "Your rehearsals are done: a Like now leads to a real submission after the hold, when every check passes.";
  const likes =
    left === 1
      ? "Your next Like is a rehearsal"
      : `Your next ${left} Likes are rehearsals`;
  return (
    `${likes}: the app fills the form and checks it, submits nothing, ` +
    `and asks you "Does this look right?" in Applications. ` +
    `Real submissions start after you answered Yes to ${left === 1 ? "it" : "each of them"}.`
  );
}

/** "1 submitted, 2 held, 1 sent to you for review", or null when nothing happened today. */
export function summaryLine(summary: Summary): string | null {
  const reviewed = summary.sent_to_review.reduce((n, one) => n + one.count, 0);
  if (summary.submitted + summary.held + reviewed === 0) return null;
  return [
    `${summary.submitted} submitted`,
    `${summary.held} held`,
    `${reviewed} sent to you for review`,
  ].join(", ");
}

/** The longest hold the api takes, in minutes (api/app/apply/settings_router.py, 86,400 s). */
export const HOLD_MAX_MINUTES = 1440;

/** A whole number of minutes from `min` to the api's longest hold, or null. */
export function readMinutes(text: string, min: number): number | null {
  const digits = text.trim();
  if (!/^[0-9]{1,4}$/.test(digits)) return null;
  const value = Number(digits);
  return value >= min && value <= HOLD_MAX_MINUTES ? value : null;
}
