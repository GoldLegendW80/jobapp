import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { applicationsKey, recentApplicationsKey } from "./apply-api";
import {
  previewBulkApply,
  startBulkApply,
  type BulkPreview,
  type BulkStarted,
} from "./bulk-apply-api";
import { queueKey } from "./queue-api";

/**
 * What "Apply to N" would do for the selected postings: one read per opening (`key`), never
 * read again by itself, so the list never moves under the person's eyes.
 */
export function useBulkPreview(
  selection: { ids: readonly string[]; key: number } | null,
) {
  return useQuery({
    queryKey: ["apply", "bulk-preview", selection?.key ?? 0],
    queryFn: ({ signal }) => previewBulkApply(selection?.ids ?? [], signal),
    enabled: selection !== null,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/**
 * "Apply to N" for exactly the postings `shown`; whatever the answer, the recent applications,
 * the queue and each started posting's application are read again.
 */
export function useStartBulkApply(
  handlers: Pick<
    UseMutationOptions<BulkStarted, Error, BulkPreview>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: startBulkApply,
    ...handlers,
    onSettled: (started) => {
      void queryClient.invalidateQueries({ queryKey: recentApplicationsKey });
      void queryClient.invalidateQueries({ queryKey: queueKey });
      for (const result of started?.results ?? []) {
        if (result.started)
          void queryClient.invalidateQueries({
            queryKey: applicationsKey(result.posting_id),
          });
      }
    },
  });
}
