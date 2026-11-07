/**
 * The selection of the map (owner, 2026-11-06: "in the job list and map, i want being able with
 * the multi select to apply at all selected"): the postings ticked in the places' lists, kept
 * from one place to the next, cleared when the filters change, never more than the bulk cap.
 * The Jobs list's bar acts on it (`BulkBar`).
 */
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { BULK_MAX } from "../api/jobs-api";
import type { BulkSelection } from "../helpers/selection";

export interface MapSelection extends BulkSelection {
  selected: ReadonlySet<string>;
  toggle: (id: string) => void;
  /** Tick (`on`) or untick every posting of `ids`, a place's list. */
  setMany: (ids: readonly string[], on: boolean) => void;
}

const CAPPED = `At most ${String(BULK_MAX)} postings can be selected: a bulk action takes ${String(BULK_MAX)} at a time.`;

export function useMapSelection(apiQuery: string): MapSelection {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [view, setView] = useState(apiQuery);
  if (view !== apiQuery) {
    setView(apiQuery);
    setSelected(new Set());
  }

  const clear = useCallback(() => {
    setSelected(new Set());
  }, []);

  const setMany = useCallback(
    (ids: readonly string[], on: boolean) => {
      const next = new Set(selected);
      let capped = false;
      for (const id of ids) {
        if (!on) next.delete(id);
        else if (next.size < BULK_MAX) next.add(id);
        else if (!next.has(id)) capped = true;
      }
      if (capped) toast.warning(CAPPED);
      setSelected(next);
    },
    [selected],
  );

  const toggle = useCallback(
    (id: string) => {
      setMany([id], !selected.has(id));
    },
    [selected, setMany],
  );

  const ids = useMemo(() => [...selected], [selected]);
  return {
    selected,
    ids,
    toggle,
    setMany,
    clear,
    cappedFrom: null,
    selectingAll: false,
  };
}
