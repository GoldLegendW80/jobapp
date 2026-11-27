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
