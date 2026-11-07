import {
  useMutation,
  useQueries,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { jobsKey } from "@/features/jobs/api/jobs-api";

import { approvalKey, getApproval } from "./apply-api";
import { approveReady, queueKey, type ApprovedReady } from "./queue-api";

/** The filled form of each application, the same reads as each queue card's. */
export function useApprovals(ids: readonly string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: approvalKey(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => getApproval(id, signal),
    })),
  });
}

type ReadyItem = { id: string; answersHash: string };

/**
 * Approve all ready, for exactly the `items` the confirmation named; whatever the answer, the
 * queue, the jobs and each item's form are read again.
 */
export function useApproveReady<Item extends ReadyItem>(
  handlers: Pick<
    UseMutationOptions<ApprovedReady, Error, Item[]>,
    "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (items: Item[]) => approveReady(items),
    ...handlers,
    onSettled: (_done, _error, items) => {
      void client.invalidateQueries({ queryKey: queueKey });
      void client.invalidateQueries({ queryKey: jobsKey });
      for (const one of items) {
        void client.invalidateQueries({ queryKey: approvalKey(one.id) });
      }
    },
  });
}
