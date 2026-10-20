/**
 * The selection of the jobs list (checkboxes, Shift ranges, everything in the view up to the
 * bulk cap) and the wording of a rescore's result.
 */
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/lib/api";

import {
  BULK_MAX,
  collectIds,
  type JobListItem,
  type RescoreResult,
} from "./api";
import type { ListSelection } from "./jobs-list";

export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;

/* --- selection ------------------------------------------------------------------------- */

export interface Selection extends ListSelection {
  ids: string[];
  clear: () => void;
  /** Set when "select all" stopped at the cap: how many matched. */
  cappedFrom: number | null;
  selectingAll: boolean;
}

/**
 * The selected rows of the list. Cleared when the view (the api query) changes. Never more
 * than BULK_MAX ids, the most one bulk call takes.
 */
export function useSelection(
  items: JobListItem[],
  apiQuery: string,
  total: number,
): Selection {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [cappedFrom, setCappedFrom] = useState<number | null>(null);
  const [selectingAll, setSelectingAll] = useState(false);
  const [view, setView] = useState(apiQuery);
  if (view !== apiQuery) {
    setView(apiQuery);
    setSelected(new Set());
    setAnchor(null);
    setCappedFrom(null);
  }

  const clear = useCallback(() => {
    setSelected(new Set());
    setAnchor(null);
    setCappedFrom(null);
  }, []);

  const toggle = useCallback(
    (index: number, range: boolean) => {
      const job = items[index];
      if (!job) return;
      const select = !selected.has(job.id);
      const from = range && anchor !== null ? Math.min(anchor, index) : index;
      const to = range && anchor !== null ? Math.max(anchor, index) : index;
      const next = new Set(selected);
      let capped = false;
      for (let i = from; i <= to; i += 1) {
        const id = items[i]?.id;
        if (!id) continue;
        if (!select) next.delete(id);
        else if (next.size < BULK_MAX) next.add(id);
        else if (!next.has(id)) capped = true;
      }
      if (capped)
        toast.warning(
          `At most ${BULK_MAX} postings can be selected: a bulk action takes ${BULK_MAX} at a time.`,
        );
      setSelected(next);
      setAnchor(index);
      setCappedFrom(null);
    },
    [items, selected, anchor],
  );

  const allState: Selection["allState"] =
    selected.size === 0
      ? "none"
      : selected.size >= Math.min(total, BULK_MAX)
        ? "all"
        : "some";

  const toggleAll = useCallback(() => {
    if (selected.size > 0) {
      clear();
      return;
    }
    setSelectingAll(true);
    collectIds(apiQuery, BULK_MAX).then(
      ({ ids, total: matching }) => {
        setSelectingAll(false);
        setSelected(new Set(ids));
        setAnchor(null);
        setCappedFrom(matching > ids.length ? matching : null);
      },
      (error: unknown) => {
        setSelectingAll(false);
        toast.error(
          `The postings could not be selected. ${describeError(error)}`,
        );
      },
    );
  }, [apiQuery, selected.size, clear]);

  const ids = useMemo(() => [...selected], [selected]);
  return {
    selected,
    ids,
    toggle,
    toggleAll,
    allState,
    clear,
    cappedFrom,
    selectingAll,
  };
}

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
