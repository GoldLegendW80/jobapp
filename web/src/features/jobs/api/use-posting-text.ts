/**
 * Jobs J31b: the text the person pastes for a posting seen only in a list (PUT /api/jobs/{id}/text).
 * The answer replaces the cached posting (its mention gone, its fit and history new), and the
 * lists are read again; a 409 (its page read meanwhile) reads the posting again.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { isApiError } from "@/lib/api";

import { jobKey, jobsKey, setPostingText, type JobDetail } from "./jobs-api";

export function useSetPostingText(
  id: string,
  options: { onSuccess?: (job: JobDetail) => void } = {},
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => setPostingText(id, text),
    onSuccess: (job) => {
      client.setQueryData(jobKey(id), job);
      void client.invalidateQueries({ queryKey: [...jobsKey, "list"] });
      options.onSuccess?.(job);
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409)
        void client.invalidateQueries({ queryKey: jobKey(id) });
    },
  });
}
