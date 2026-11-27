/**
 * The selection of the jobs list: checkboxes, Shift ranges, everything in the view up to the
 * bulk cap.
 */
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/lib/api";

import { BULK_MAX, type JobListItem } from "../api/jobs-api";
import { useCollectIds } from "../api/use-jobs";
import type { Selection } from "../helpers/selection";

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
  // The seen-in-a-list postings "select all" found beyond the rows loaded (jobs J31).
  const [collectedListed, setCollectedListed] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [view, setView] = useState(apiQuery);
  const collectIds = useCollectIds();
  if (view !== apiQuery) {
    setView(apiQuery);
    setSelected(new Set());
    setAnchor(null);
    setCappedFrom(null);
    setCollectedListed(new Set());
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
      ({ ids, total: matching, listed }) => {
        setSelectingAll(false);
        setSelected(new Set(ids));
        setCollectedListed(new Set(listed));
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
  }, [apiQuery, selected.size, clear, collectIds]);

  const ids = useMemo(() => [...selected], [selected]);
  const listed = useMemo(() => {
    const known = new Set(collectedListed);
    for (const item of items) if (item.list_url !== null) known.add(item.id);
    return new Set(ids.filter((id) => known.has(id)));
  }, [ids, items, collectedListed]);
  return {
    listed,
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
