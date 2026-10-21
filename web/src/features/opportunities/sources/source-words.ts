/**
 * The words of the freelance and project feeds (opportunities O7 and O8): their names when the
 * catalogue cannot be read, the name of the `source` their postings are recorded with, and what
 * each error code of a feed in a sweep's summary means for the person. Pure, so it is tested
 * alone. No dash is used as a separator in any of them.
 */
import { FEED_KINDS, type Feed } from "@/features/profile/api";

export type FeedKind = Feed["kind"];

export const isFeedKind = (kind: string): kind is FeedKind =>
  (FEED_KINDS as readonly string[]).includes(kind);

/** The feeds by kind, as the catalogue names them; used when the catalogue cannot be read. */
export const FEED_NAMES: Record<FeedKind, string> = {
  remoteok: "Remote OK",
  weworkremotely: "We Work Remotely",
  hackernews_freelance: "Hacker News: Freelancer? Seeking freelancer?",
  hackernews_hiring: "Hacker News: Who is hiring?",
};

/** The short name of a feed, in a sentence or a list ("Hacker News, freelance thread"). */
export const FEED_SHORT_NAMES: Record<FeedKind, string> = {
  remoteok: "Remote OK",
  weworkremotely: "We Work Remotely",
  hackernews_freelance: "Hacker News, freelance thread",
  hackernews_hiring: "Hacker News, hiring thread",
};

/** The `source` of a feed's postings (api sources.SOURCES), named as a person reads it. */
const SOURCE_NAMES: Record<string, string> = {
  remoteok: FEED_SHORT_NAMES.remoteok,
  weworkremotely: FEED_SHORT_NAMES.weworkremotely,
  "hackernews:freelance": FEED_SHORT_NAMES.hackernews_freelance,
  "hackernews:hiring": FEED_SHORT_NAMES.hackernews_hiring,
};

/** The name of a feed's `source` ("remoteok" is "Remote OK"), or null for any other source. */
export function feedSourceName(source: string): string | null {
  return SOURCE_NAMES[source.trim()] ?? null;
}

/** What the sweep calls a feed while it reads it (api sources.NAMES, the `current_board`). */
const READING_NAMES = new Set([
  "Remote OK",
  "We Work Remotely",
  "Hacker News, Freelancer? Seeking freelancer?",
  "Hacker News, Who is hiring?",
]);

export const isFeedReading = (current: string): boolean =>
  READING_NAMES.has(current);

/** "via Remote OK" when a posting's first source is a feed, else null. */
export function viaFeed(source: string | null | undefined): string | null {
  const first = (source ?? "").split(", ")[0] ?? "";
  const name = feedSourceName(first);
  return name ? `via ${name}` : null;
}

/** What each `error` of a feed in a sweep's `sources` means (api sources.ERRORS). */
export const FEED_PROBLEMS: Record<string, string> = {
  not_found: "The feed was not found at its address, so nothing was read.",
  blocked: "The site refused to answer the app, so nothing was read.",
  rate_limited:
    "The site asked the app to slow down, so it is read again at the next search.",
  upstream_error:
    "The site had a problem on its side, so it is read again at the next search.",
  timeout:
    "The site took too long to answer, so it is read again at the next search.",
  network:
    "The site could not be reached, so it is read again at the next search.",
  too_large: "The feed was too large to read, so nothing was kept from it.",
  invalid_response:
    "The feed did not read as expected, so nothing was kept from it.",
  too_many:
    "The feed held too many postings to read at once, so nothing was kept from it.",
  store_failed:
    "What this feed found could not be saved, so nothing was kept from it.",
  time_budget:
    "The search ran out of time before this feed was read to the end. What was read is kept.",
  stub_missing:
    "This server is a test server that reads no real feed, so it was not read.",
};

export const FEED_PROBLEM_UNKNOWN = "It could not be read this time.";

export const feedProblem = (code: string): string =>
  FEED_PROBLEMS[code] ?? FEED_PROBLEM_UNKNOWN;

/** The postings a never apply rule set aside, as a sentence; empty when there were none. */
export function setAsideByRules(count: number): string {
  if (count <= 0) return "";
  return count === 1
    ? "1 posting was set aside by your roles to never apply for."
    : `${count.toLocaleString("en-GB")} postings were set aside by your roles to never apply for.`;
}

/** A feed's line in a sweep's summary: found, new, set aside by the rules, and why it stopped. */
export function feedLine(source: {
  found: number;
  new: number;
  skipped_by_rule: number;
  error: string | null;
}): string {
  const found = source.found.toLocaleString("en-GB");
  const counts = `${found} ${source.found === 1 ? "posting" : "postings"} found, ${source.new.toLocaleString("en-GB")} new.`;
  return [
    counts,
    setAsideByRules(source.skipped_by_rule),
    source.error ? feedProblem(source.error) : "",
  ]
    .filter(Boolean)
    .join(" ");
}
