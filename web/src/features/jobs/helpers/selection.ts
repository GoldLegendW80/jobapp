/**
 * What a selection of postings offers the list and the bulk bar (use-selection.ts and
 * use-map-selection.ts make them), a count in words, and the wording of a rescore's result.
 */
import type { RescoreResult } from "../api/jobs-api";

export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;

/* --- selection ------------------------------------------------------------------------- */

/** What the jobs list needs to show and change the selection (jobs-page.tsx owns it). */
export interface ListSelection {
  selected: ReadonlySet<string>;
  /** A click on item `index`'s checkbox; `range` when Shift was held. */
  toggle: (index: number, range: boolean) => void;
  /** The header checkbox: everything in the current filter, or nothing. */
  allState: "none" | "some" | "all";
  toggleAll: () => void;
}

/** What the bulk bar needs of a selection: the Jobs list's (`useSelection`) or the map's. */
export interface BulkSelection {
  ids: string[];
  clear: () => void;
  /** Set when "select all" stopped at the cap: how many matched. */
  cappedFrom: number | null;
  selectingAll: boolean;
}

export type Selection = ListSelection & BulkSelection;

export function rescoreSummary(result: RescoreResult): string {
  const parts = [
    plural(result.changed, "changed", "changed"),
    plural(result.unchanged, "unchanged", "unchanged"),
  ];
  if (result.skipped_manual)
    parts.push(`${plural(result.skipped_manual, "fit")} set by hand kept`);
  if (result.skipped_no_text)
    parts.push(
      `${plural(result.skipped_no_text, "posting")} without text skipped`,
    );
  const busy = result.skipped_busy
    ? ` ${plural(result.skipped_busy, "posting")} ${result.skipped_busy === 1 ? "was" : "were"} busy and kept ${result.skipped_busy === 1 ? "its" : "their"} score. Rescore again to include ${result.skipped_busy === 1 ? "it" : "them"}.`
    : "";
  return `Rescored ${plural(result.scored, "posting")}: ${parts.join(", ")}.${busy}`;
}
