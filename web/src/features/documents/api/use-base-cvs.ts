import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  baseCvsKey,
  deleteBaseCv,
  listBaseCvs,
  patchBaseCv,
} from "./base-cvs-api";
import { documentKey, versionsKey, type Generation } from "./documents-api";
import { templatesKey } from "./templates-api";
import { isOpen, pollInterval } from "../helpers/labels";

/** Reads the base CVs again, after a change to one of them. */
export function useRefreshBaseCvs() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: baseCvsKey });
}

/**
 * The person's base CVs, read again while one of them is written, rendered or captured. When a
 * design capture ends, the document of that CV (and on success its versions and the templates)
 * is read again too.
 */
export function useBaseCvs() {
  const client = useQueryClient();
  const list = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
    refetchInterval: (current) => {
      const waits = (current.state.data?.base_cvs ?? [])
        .map((one) =>
          pollInterval(one.render_state, one.generation, one.design_generation),
        )
        .filter((wait) => wait !== false);
      return waits.length > 0 ? Math.min(...waits) : false;
    },
  });
  // The opened view may be absent when capture ends. A fresh tab also reconciles terminal
  // captures, since its document cache can predate the current list even after a remount.
  const captures = useRef(new Map<string, Generation | null>());
  useEffect(() => {
    if (list.data === undefined) return;
    const previous = captures.current;
    captures.current = new Map(
      list.data.base_cvs.map((cv) => [cv.id, cv.design_generation]),
    );
    for (const cv of list.data.base_cvs) {
      const capture = cv.design_generation;
      const before = previous.get(cv.id);
      const ended =
        !isOpen(capture) &&
        (isOpen(before) ||
          (capture !== null &&
            (capture.id !== before?.id || capture.status !== before.status)));
      if (!ended) continue;
      void client.invalidateQueries(
        { queryKey: documentKey(cv.id), exact: true },
        { cancelRefetch: false },
      );
      if (capture?.status === "done") {
        void client.invalidateQueries({ queryKey: versionsKey(cv.id) });
        void client.invalidateQueries({ queryKey: templatesKey });
      }
    }
  }, [client, list.data]);
  return list;
}

/** Renames a CV or makes it the default; the list is read again before `onUpdated`. */
export function useUpdateBaseCv(
  baseId: string,
  on: { onUpdated?: () => void; onError?: (error: Error) => void } = {},
) {
  const refresh = useRefreshBaseCvs();
  return useMutation({
    mutationFn: (change: { name?: string; default?: boolean }) =>
      patchBaseCv(baseId, change),
    onSuccess: async () => {
      await refresh();
      on.onUpdated?.();
    },
    onError: on.onError,
  });
}

/**
 * Deletes a CV. `onEnd` comes first, on either outcome; on success the CV's document leaves the
 * cache and the list is read again before `onDeleted`; on failure the list is read again after
 * `onError`.
 */
export function useDeleteBaseCv(
  baseId: string,
  on: {
    onEnd: () => void;
    onDeleted: () => void;
    onError: (error: Error) => void;
  },
) {
  const client = useQueryClient();
  const refresh = useRefreshBaseCvs();
  return useMutation({
    mutationFn: () => deleteBaseCv(baseId),
    onSuccess: async () => {
      on.onEnd();
      client.removeQueries({ queryKey: documentKey(baseId) });
      await refresh();
      on.onDeleted();
    },
    onError: (error) => {
      on.onEnd();
      on.onError(error);
      void refresh();
    },
  });
}

/** The person's base CVs as last read, without following their work: for a picker. */
export function useBaseCvList() {
  return useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
  });
}
