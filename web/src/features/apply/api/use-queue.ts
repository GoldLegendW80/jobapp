import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useEffect } from "react";

import type { Application } from "./apply-api";
import {
  answerRehearsal,
  getNeedsCount,
  getQueue,
  getQueueCount,
  getSummary,
  needsCountKey,
  queueCountKey,
  queueListKey,
  summaryKey,
} from "./queue-api";
import { serverOffset } from "../helpers/full-auto-state";
import { COUNT_POLL_MS, queuePoll } from "../helpers/queue-state";

/** The summary is read again this often while the queue page is open. */
const SUMMARY_POLL_MS = 30_000;

/**
 * The person's applications, read again by itself while something is under way; each read also
 * sets the app shell's count, so both say the same.
 */
export function useQueue() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: queueListKey,
    queryFn: ({ signal }) => getQueue(signal),
    refetchInterval: (current) =>
      queuePoll(
        current.state.data,
        Date.now(),
        serverOffset(current.state.data?.now, current.state.dataUpdatedAt),
      ),
  });
  const waitingOnYou = query.data?.waiting_on_you;
  useEffect(() => {
    if (waitingOnYou !== undefined) {
      client.setQueryData(queueCountKey, waitingOnYou);
    }
  }, [waitingOnYou, client]);
  return query;
}

/** How many applications wait for the person (the app shell's badge). */
export function useQueueCount() {
  return useQuery({
    queryKey: queueCountKey,
    queryFn: ({ signal }) => getQueueCount(signal),
    refetchInterval: COUNT_POLL_MS,
  });
}

/** How many of the person's applications need them (the app shell's dot). */
export function useNeedsCount() {
  return useQuery({
    queryKey: needsCountKey,
    queryFn: ({ signal }) => getNeedsCount(signal),
    refetchInterval: COUNT_POLL_MS,
  });
}

/** Full auto's day so far. */
export function useQueueSummary() {
  return useQuery({
    queryKey: summaryKey,
    queryFn: ({ signal }) => getSummary(signal),
    refetchInterval: SUMMARY_POLL_MS,
  });
}

/** Yes (true) or No to a Full auto rehearsal; whatever the answer, Apply is read again. */
export function useAnswerRehearsal(
  applicationId: string,
  handlers: Pick<
    UseMutationOptions<Application, Error, boolean>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (yes: boolean) =>
      answerRehearsal(applicationId, yes ? "yes" : "no"),
    ...handlers,
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["apply"] });
    },
  });
}
