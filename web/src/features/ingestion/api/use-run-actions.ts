import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  actionFailure,
  cancelRun,
  continueRefusal,
  continueRun,
  refreshRuns,
  resumeRun,
  runKey,
  tokens,
  type Run,
} from "./runs-api";

export interface RunAction {
  name: "resume" | "cancel" | "continue";
  /** Continue only: the tokens on screen, the figure agreed to and the one sent. */
  adds?: number;
}

/**
 * Resume, Cancel and Continue of a run. A Continue refused because its figure had changed says
 * so in `changed` and shows the figure of now: it is never a success.
 */
export function useRunActions(runId: string) {
  const client = useQueryClient();
  const [failure, setFailure] = useState<string>();
  const [changed, setChanged] = useState<string>();

  const settle = async (updated: Run) => {
    // A read sent before the click would land after this answer and bring the old state back.
    await client.cancelQueries({ queryKey: runKey(updated.id) });
    client.setQueryData(runKey(updated.id), updated);
    refreshRuns(client);
  };
  const action = useMutation({
    mutationFn: (what: RunAction) =>
      what.name === "continue"
        ? continueRun(runId, what.adds ?? 0)
        : what.name === "resume"
          ? resumeRun(runId)
          : cancelRun(runId),
    onMutate: () => {
      setFailure(undefined);
      setChanged(undefined);
    },
    onSuccess: settle,
    onError: (error, what) => {
      const refusal =
        what.name === "continue" ? continueRefusal(error) : undefined;
      if (refusal) {
        if (refusal.run) void settle(refusal.run);
        else void client.invalidateQueries({ queryKey: runKey(runId) });
        setChanged(
          refusal.continueAdds > 0
            ? `Nothing was added: the figure has changed. Continue now adds ${tokens(refusal.continueAdds)} tokens. Click it again if you agree.`
            : "Nothing was added: nothing is left to read, so the run is closing.",
        );
        return;
      }
      setFailure(
        actionFailure(error, "That did not go through. Try again in a moment."),
      );
      void client.invalidateQueries({ queryKey: runKey(runId) });
    },
  });

  return {
    act: action.mutate,
    busy: action.isPending,
    failure,
    changed,
  };
}
