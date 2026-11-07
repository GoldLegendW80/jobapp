/**
 * The fact base behind the fact parts of About you: its size against the api's cap (schemas.py,
 * 200 KB of UTF-8 in the cleaned titles and bodies) and the order of its blocks, which follows
 * the order of the fact parts (owner, 2026-11-07). A part's block is the one of its title
 * (api/app/profile/about_text.py `block_title`).
 */
import type { FactBlock } from "../api/profile-api";
import { keyOf, type About, type Part } from "../api/about-api";
import { shownPart } from "./about-parts";

export const FACTS_MAX_BYTES = 200_000;
/** From this share of the cap the meter warns. */
export const FACTS_WARN_SHARE = 0.9;

const encoder = new TextEncoder();
export const utf8Bytes = (value: string) => encoder.encode(value).length;

const cleanLine = (value: string) => value.normalize("NFC").trim();
const cleanText = (value: string) =>
  value.replace(/\r\n?/g, "\n").normalize("NFC").trim();

/** Bytes of the fact base as the api counts them: UTF-8 of every cleaned title and body. */
export function factBytes(blocks: readonly FactBlock[]): number {
  return blocks.reduce(
    (sum, b) =>
      sum + utf8Bytes(cleanLine(b.title)) + utf8Bytes(cleanText(b.body)),
    0,
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} bytes`;
  return `${(bytes / 1000).toLocaleString("en", { maximumFractionDigits: 1 })} KB`;
}

/** "12.3 KB of 200 KB", or exact bytes near and over the cap, where rounding would hide it. */
export function sizeText(bytes: number, cap: number): string {
  if (bytes >= cap * 0.99) {
    return `${bytes.toLocaleString("en")} of ${cap.toLocaleString("en")} bytes`;
  }
  return `${formatBytes(bytes)} of ${formatBytes(cap)}`;
}

/** The headings the api reads as skills or answers (about_text.py HEADINGS); any other is facts. */
const OTHER_KINDS = new Set(["skills", "compétences", "answers", "réponses"]);
const NOTES = "Notes: ";

/** The title of the block a fact part is: its heading, less the prefix the api may have added. */
export function blockTitle(heading: string): string {
  const rest = heading.startsWith(NOTES)
    ? heading.slice(NOTES.length)
    : heading;
  return OTHER_KINDS.has(keyOf(rest)) ? rest : heading;
}

/** Whether a part of the document is a fact part (an unsaved part counts as one). */
export const isFactPart = (about: About, part: Part) =>
  (shownPart(about, part.heading)?.kind ?? "facts") === "facts";

/**
 * The fact base once every fact part is saved: a block per fact part as typed, and the stored
 * blocks no part holds. What the meter measures, before the api would refuse it.
 */
export function factBaseOf(
  parts: readonly Part[],
  about: About,
  stored: readonly FactBlock[],
): FactBlock[] {
  const missing = new Set(about.missing_blocks.map(keyOf));
  return [
    ...parts
      .filter((part) => isFactPart(about, part))
      .map((part) => ({ title: blockTitle(part.heading), body: part.body })),
    ...stored.filter((block) => missing.has(keyOf(block.title))),
  ];
}

/**
 * The stored blocks in the order of `titles` (the fact parts, top to bottom): the blocks a title
 * names trade places among themselves, the others keep theirs. Null when nothing moves.
 */
export function orderBlocks(
  blocks: readonly FactBlock[],
  titles: readonly string[],
): FactBlock[] | null {
  const rank = new Map(titles.map((title, index) => [keyOf(title), index]));
  const placeOf = (block: FactBlock) => rank.get(keyOf(block.title));
  const named = blocks
    .filter((block) => placeOf(block) !== undefined)
    .sort((a, b) => (placeOf(a) ?? 0) - (placeOf(b) ?? 0));
  let next = 0;
  const ordered = blocks.map((block) =>
    placeOf(block) === undefined ? block : (named[next++] ?? block),
  );
  return ordered.every((block, index) => block === blocks[index])
    ? null
    : ordered;
}
