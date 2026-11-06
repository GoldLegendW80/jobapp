import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { describeError, errorDetail } from "@/lib/api";

import { cancelRun, runKey, runsKey } from "./runs-api";

/** Cancel of one run: says whether it stopped at once or is stopping, then reads it again. */
export function useCancelRun(runId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => cancelRun(runId),
    onSuccess: (updated) => {
      toast.info(
        updated.status === "cancelled" ? "Run cancelled." : "Stopping the run.",
      );
      void client.invalidateQueries({ queryKey: runKey(runId) });
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    onError: (error) => {
      toast.error(errorDetail(error) ?? describeError(error));
      void client.invalidateQueries({ queryKey: runKey(runId) });
    },
  });
}
