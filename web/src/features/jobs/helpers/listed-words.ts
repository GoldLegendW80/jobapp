/**
 * Jobs J31 (owner, 2026-11-26: "Oui, avec une mention"): a posting seen only in a list of results
 * is kept from its card, its own page not read (`list_url`, the list's address; null for any
 * other posting). Its words on the screens, the listing filter's choices, and the reasons the app
 * refused a card (api/app/browser/finder.py, `report_listing`).
 */
import { plural } from "./selection";

export const LISTED_MENTION = "Seen in a list: its own page was not read";

/** The api's sentence of the 409 of Write now, Apply and a Like (service.LISTED_WAITS). */
export const LISTED_WAITS =
  "This posting was seen only in a list: its text is needed first. It is ready once a search reads its own page.";

/** Whether a posting was seen only in a list, its text the card's. */
export const isListed = (job: { list_url?: string | null }) =>
  typeof job.list_url === "string";

export const LISTING_CHOICES = ["only", "without"] as const;
export type ListingChoice = (typeof LISTING_CHOICES)[number];
export const LISTING_CHOICE_LABELS: Record<ListingChoice, string> = {
  only: "Seen only in a list",
  without: "Page read",
};

/** What the bulk bar says of the selected postings Apply and Like leave out. */
export function listedSkipped(count: number): string {
  return `${plural(count, "posting")} seen only in a list ${count === 1 ? "is" : "are"} skipped: ${count === 1 ? "its" : "their"} text is needed first.`;
}

/** Why the app refused a card of a list (`cards_refused`), as the end of a sentence. */
export const CARD_REFUSALS: Record<string, string> = {
  card_not_seen:
    "the assistant had not seen its link and its title together on a page",
  not_a_posting_page: "it was a list or a start page, not a posting",
  page_error: "its page answered an error",
  page_readable: "its own page could be read, so it was not kept from its card",
  duplicate: "it was reported twice",
  missing_field: "its title or address was missing",
  not_an_address: "it had no address the app could open",
  not_a_posting: "it was not given as a posting",
};

export const OTHER_CARD_REFUSAL = "the app's check found another problem";

const total = (counts?: Record<string, number>) =>
  Object.values(counts ?? {}).reduce((sum, n) => sum + (n > 0 ? n : 0), 0);

/** "3 cards of a list refused by the app's check: 2 because ...; 1 because ...". */
export function cardsRefusedSentence(refused?: Record<string, number>): string {
  const reasons = Object.entries(refused ?? {}).filter(([, n]) => n > 0);
  if (!reasons.length) return "";
  const why = reasons
    .map(
      ([reason, n]) =>
        `${n.toLocaleString("en-GB")} because ${
          Object.hasOwn(CARD_REFUSALS, reason)
            ? CARD_REFUSALS[reason]
            : OTHER_CARD_REFUSAL
        }`,
    )
    .join("; ");
  return `${plural(total(refused), "card")} of a list refused by the app's check: ${why}.`;
}

/** The postings seen in a list before whose own page a search read this time. */
export function listedReadSentence(count?: number): string {
  if (!count || count <= 0) return "";
  return `${plural(count, "posting")} seen only in a list before had ${count === 1 ? "its" : "their"} own page read: ${count === 1 ? "its" : "their"} text is now the page's.`;
}

export const countedCards = total;

/** Jobs J36: on a posting whose own page the app failed to read on 3 different days. */
export const LISTED_GIVEN_UP =
  "The app tried to read its own page on 3 different days and no longer tries: paste its text to use it.";

/** Why the app's reading of the pages of postings seen in a list ended (api's listed_pages.ENDS),
 * said of the postings still waiting for their page. */
export const LISTED_PAGES_ENDS: Record<string, string> = {
  done: "the next search tries again",
  time_bound: "the time set for reading them ran out",
  browser_wanted: "an application needed the browser",
  walled: "the website showed a sign in page or a security check",
  memory: "the browser ran short of memory",
  permission: "the website's permission was taken back",
  store_failed: "the search ended",
  browser_lost: "the browser was no longer available",
  failed: "the app met a problem",
};

export interface ListedPages {
  read: number;
  closed: number;
  unread: number;
  given_up: number;
  left: number | null;
  ended: string;
}

/** Jobs J36: what the app read by itself after a website's agent, and what still waits, or "". */
export function listedPagesSentence(pages?: ListedPages | null): string {
  if (!pages) return "";
  const opened = pages.read + pages.closed + pages.unread;
  const left = pages.left ?? 0;
  if (opened <= 0 && left <= 0) return "";
  const figure = (n: number) => n.toLocaleString("en-GB");
  const given =
    pages.given_up > 0 ? ` (${figure(pages.given_up)} no longer tried)` : "";
  const parts = [
    pages.read > 0 ? `${figure(pages.read)} read` : "",
    pages.closed > 0 ? `${figure(pages.closed)} closed, as gone` : "",
    pages.unread > 0 ? `${figure(pages.unread)} could not be read${given}` : "",
  ].filter((part) => part !== "");
  const done = opened
    ? `After the search, the app opened the pages of ${plural(opened, "posting")} seen only in a list: ${parts.join(", ")}.`
    : "";
  const why = Object.hasOwn(LISTED_PAGES_ENDS, pages.ended)
    ? LISTED_PAGES_ENDS[pages.ended]
    : LISTED_PAGES_ENDS.failed;
  const wait = left
    ? `${plural(left, "posting")} seen only in a list still ${left === 1 ? "waits" : "wait"} for ${left === 1 ? "its" : "their"} page: ${why ?? ""}.`
    : "";
  return [done, wait].filter((part) => part !== "").join(" ");
}
