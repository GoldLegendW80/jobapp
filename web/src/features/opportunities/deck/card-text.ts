/**
 * The words of a deck card (SPEC-opportunities.md, "The card"), pure so they are tested alone.
 * No dash is used as a separator in any of them.
 */
import { WORDING, contractWord } from "@/lib/wording";

import type { Card, Mode } from "../opportunities-api";

/** The names of the sources a posting's `source` starts with ("greenhouse:acme" is Greenhouse). */
const SOURCE_NAMES: Record<string, string> = {
  ashby: "Ashby",
  greenhouse: "Greenhouse",
  lever: "Lever",
  linkedin: "LinkedIn",
  welcometothejungle: "Welcome to the Jungle",
  remoteok: "Remote OK",
  weworkremotely: "We Work Remotely",
  hackernews: "Hacker News",
};

/** Sources that are no place a person would name: the posting was added or imported. */
const NO_SOURCE = new Set(["manual", "import"]);

/** "via Greenhouse", or null when the posting came from no outside source. */
export function sourceLine(source: string | null): string | null {
  const key = (source ?? "").split(":")[0]?.trim().toLowerCase() ?? "";
  if (!key || NO_SOURCE.has(key)) return null;
  const name = SOURCE_NAMES[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
  return `via ${name}`;
}

const WORK_MODES: Record<string, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
};

/** The location, or "Remote" or "Hybrid" when the posting states its work mode. */
export function placeLine(card: Pick<Card, "location" | "work_mode">): string {
  const mode = card.work_mode ? WORK_MODES[card.work_mode] : undefined;
  const location = card.location.trim();
  if (mode && location && !location.toLowerCase().includes(mode.toLowerCase()))
    return `${location}, ${mode}`;
  return mode ?? location;
}

export const contractLine = (card: Pick<Card, "contract_type">) =>
  contractWord(card.contract_type);

/** The match reason as one sentence; null when the posting names nothing to match. */
export function reasonLine(card: Pick<Card, "reason">): string | null {
  const { text, matches, gap } = card.reason;
  if (text) return text;
  const parts: string[] = [];
  if (matches.length > 0) parts.push(`Matches: ${matches.join(", ")}.`);
  if (gap) parts.push(`Gap: ${gap}.`);
  return parts.length > 0 ? parts.join(" ") : null;
}

/** What a screen reader hears for the card in one go. */
export function cardLabel(card: Card): string {
  return [
    `${card.title} at ${card.company}`,
    placeLine(card),
    contractLine(card),
    card.pay ?? "",
    card.fit === null ? "Not scored yet" : `Fit ${card.fit}`,
  ]
    .filter(Boolean)
    .join(", ");
}

export const MODE_WORDS: Record<Mode, string> = {
  review: WORDING.reviewFirst,
  full_auto: WORDING.fullAuto,
};

/** The toast after a Like, in the mode the deck said a Like runs in. */
export const LIKED: Record<Mode, string> = {
  review: "Liked. Your documents are being written.",
  full_auto: "Liked. The app will apply after the hold unless you cancel.",
};

export const SWIPE_TEXT = {
  passed: "Passed.",
  gone: "This posting is no longer there.",
  undone: "Undone.",
  tooLate: "Too late to undo: the application is already being sent.",
  passOffered: "You can pass this posting instead.",
} as const;
