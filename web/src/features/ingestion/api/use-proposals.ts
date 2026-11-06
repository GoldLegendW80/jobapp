import { useQuery } from "@tanstack/react-query";

import {
  completenessKey,
  finishedRunKey,
  getCompleteness,
  getFinishedRunId,
  getProposals,
  proposalsKey,
} from "./proposals-api";

/** The id of the person's latest finished run, or null while none has finished. */
export function useFinishedRunId(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: finishedRunKey,
    queryFn: ({ signal }) => getFinishedRunId(signal),
    ...options,
  });
}

/** What the run `runId` proposes for the profile; nothing is asked while it is undefined. */
export function useProposals(runId: string | undefined) {
  return useQuery({
    queryKey: proposalsKey(runId ?? ""),
    queryFn: ({ signal }) => getProposals(runId ?? "", signal),
    enabled: runId !== undefined,
  });
}

/** The completeness of the whole profile, in percent. */
export function useCompleteness() {
  return useQuery({
    queryKey: completenessKey,
    queryFn: ({ signal }) => getCompleteness(signal),
  });
}
