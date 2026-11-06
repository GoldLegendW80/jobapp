/** The conversation of one job: its thread (none before the first message), the messages, and
 * the run answering now. */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useEffect } from "react";

import { isApiError } from "@/lib/api";

import {
  deleteThread,
  findJobThread,
  getThread,
  jobThreadKey,
  threadKey,
} from "./chat-api";
import { runsKey } from "./runs-api";

export function useJobThread(jobId: string) {
  const client = useQueryClient();
  const found = useQuery({
    queryKey: jobThreadKey(jobId),
    queryFn: ({ signal }) => findJobThread(jobId, signal),
  });
  const threadId = found.data?.id ?? null;
  const thread = useQuery({
    queryKey: threadKey(threadId ?? "none"),
    queryFn: ({ signal }) => getThread(threadId ?? "", signal),
    enabled: threadId !== null,
  });
  const detail = threadId ? thread.data : undefined;

  // The conversation this panel knows was deleted somewhere else (another tab): its read
  // answers 404. Ask again which conversation the job has, so the panel shows the empty
  // state, where the next message starts a new one, instead of an error until a reload.
  const gone = isApiError(thread.error) && thread.error.status === 404;
  useEffect(() => {
    if (!gone || threadId === null) return;
    client.removeQueries({ queryKey: threadKey(threadId) });
    void client.invalidateQueries({
      queryKey: jobThreadKey(jobId),
      exact: true,
    });
  }, [gone, threadId, client, jobId]);

  const refresh = () => {
    if (threadId)
      void client.invalidateQueries({ queryKey: threadKey(threadId) });
    void client.invalidateQueries({ queryKey: runsKey, exact: true });
  };

  return {
    thread: found.data ?? null,
    threadId,
    detail,
    messages: detail?.messages ?? [],
    activeRun: detail?.active_run_id ?? null,
    loading: found.isPending || (threadId !== null && thread.isPending),
    failedToLoad: found.isError || thread.isError,
    loadError: found.error ?? thread.error,
    refresh,
  };
}

/** Deletes the job's conversation: the job has none until the next message. */
export function useDeleteJobThread(
  jobId: string,
  threadId: string,
  handlers: Pick<UseMutationOptions<void>, "onSuccess" | "onError">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => deleteThread(threadId),
    ...handlers,
    onSuccess: (...args) => {
      client.setQueryData(jobThreadKey(jobId), null);
      client.removeQueries({ queryKey: threadKey(threadId) });
      return handlers.onSuccess?.(...args);
    },
  });
}
