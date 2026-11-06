import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { acceptAdditions, type ProposalItem } from "./proposals-api";
import { additionsOutcome, refusal } from "../helpers/proposal-outcomes";
import { useProposalRefresh } from "./use-proposal-refresh";

/** "Accept all additions": every pending proposal on the screen that the server takes as is. */
export function useAcceptAdditions(
  runId: string,
  additions: readonly ProposalItem[],
) {
  const [outcome, setOutcome] = useState<string>();
  const refresh = useProposalRefresh(runId);
  const all = useMutation({
    mutationFn: () =>
      acceptAdditions(
        runId,
        additions.map((item) => item.id),
      ),
    onMutate: () => {
      setOutcome(undefined);
    },
    onSuccess: async (done) => {
      setOutcome(additionsOutcome(done));
      await refresh();
    },
    onError: async (error) => {
      setOutcome(refusal(error));
      await refresh();
    },
  });
  return { all, outcome };
}
