import { useQueryClient } from "@tanstack/react-query";

import { proposalsKey } from "./proposals-api";

/**
 * Reads the list and the profile again: after a decision both may have moved. The promise ends
 * once both are read, so a mutation that returns it stays pending, and its buttons disabled,
 * until the list shows what was kept.
 */
export function useProposalRefresh(runId: string) {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: proposalsKey(runId) }),
      // The profile, its completeness and the revisions of the History drawer.
      client.invalidateQueries({ queryKey: ["profile"] }),
    ]);
}
