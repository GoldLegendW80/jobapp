import { useQuery } from "@tanstack/react-query";

import { getRescoreStatus, rescoreStatusKey } from "./jobs-api";

/** How often the rescore after a save is read again while it runs. */
export const RESCORE_POLL_MS = 1500;

/** The rescore the server runs after the profile save of `revision`, read again until it ends. */
export function useRescoreStatus(revision: number) {
  return useQuery({
    // Each save reads it afresh: an answer read before this save says nothing of it.
    queryKey: [...rescoreStatusKey, revision],
    queryFn: ({ signal }) => getRescoreStatus(signal),
    refetchInterval: (current) => {
      const data = current.state.data;
      if (!data) return RESCORE_POLL_MS;
      const asked =
        data.wanted_revision !== null && data.wanted_revision >= revision;
      return asked && data.done_revision < revision ? RESCORE_POLL_MS : false;
    },
  });
}
