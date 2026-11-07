/**
 * The words of the proposal review: section, flag and status labels, the notes the page shows,
 * and a profile value or a document's excerpt as text. Everything a document wrote is rendered
 * as text, and an excerpt is cut to EXCERPT_SHOWN characters.
 */
import type { ProposalItem } from "../api/proposals-api";
import { isRecord, words } from "./proposal-value";

export const EXCERPT_SHOWN = 300;
/** Proposals of one section shown at first, and added by each "Show more". */
export const PAGE = 50;

const SECTION_NAMES: Record<string, string> = {
  identity: "Identity",
  experience: "Experience",
  facts: "Facts",
  skills: "Skills",
  search: "Search",
  answers: "Answers",
};

const FLAG_LABELS: Record<string, string> = {
  conflict: "Sources disagree",
  derived: "Calculated",
  transcribed: "Transcribed, check it",
  looks_like_instruction: "Reads like an instruction",
};

const STATUS_LABELS: Record<string, string> = {
  accepted: "Accepted",
  rejected: "Rejected",
  undone: "Taken back",
  superseded: "Replaced by a newer run",
  invalid: "No longer fits",
};

export const FREE_TEXT_NOTE =
  "The quote shows where this comes from. The wording is the agent's own, so read it before you accept.";
export const EMPTY_NOTE =
  "Nothing new to propose. What these documents say is already in your profile.";
export const NEWER_RUN_NOTE =
  "A new run is in progress. When it ends, what you have not decided here is replaced by what it proposes.";
export const TYPED_NOTE =
  "Your profile already holds your own value here, typed by you or kept from an earlier decision. Accept all leaves it for you: accept this one on its own if the document is right.";
export const REPLACE_NOTE =
  "This replaces the whole entry: what it leaves empty is cleared. Accept all leaves it for you to accept on its own.";
export const ADDITIONS_NOTE =
  "Adds what fills an empty place in your profile, contact details, languages, jobs and schools included. What would change a value you hold waits for your own click. Each one stays below with Edit, Reject and Take back.";
export const TWO_ENTRIES_NOTE =
  " These may be two entries: two positions, or two degrees, that started the same year at one place show here together. Keep one here and add the other in your profile.";

export function sectionName(section: string): string {
  return SECTION_NAMES[section] ?? words(section);
}

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? words(flag);
}

/** The badge of a decided proposal; undefined while it is pending. */
export function statusLabel(status: string): string | undefined {
  return STATUS_LABELS[status];
}

export function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

export function cut(text: string): string {
  const points = Array.from(text);
  return points.length > EXCERPT_SHOWN
    ? `${points.slice(0, EXCERPT_SHOWN).join("")}…`
    : text;
}

export function scalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) return value.map(scalar).join(", ");
  if (isRecord(value))
    return Object.entries(value)
      .filter(([, held]) => held !== null && held !== "")
      .map(([name, held]) => `${words(name)}: ${scalar(held)}`)
      .join(", ");
  return "";
}

export function itemTitle(item: ProposalItem): string {
  const value = isRecord(item.value) ? item.value : {};
  const named = [value.organisation, value.name, value.title, value.school]
    .filter((one): one is string => typeof one === "string" && one !== "")
    .at(0);
  const slot = words(item.slot);
  return named ? `${slot}: ${named}` : slot;
}
