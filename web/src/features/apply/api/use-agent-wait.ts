import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { describeError, isApiError } from "@/lib/api";

import {
  answerQuestion,
  applicationKey,
  applicationsKey,
  getApplication,
  type Application,
} from "./apply-api";
import { queueKey } from "./queue-api";
import { agentWaits, LATE_ANSWER, showsAsk } from "../helpers/agent-wait";
import { serverOffset } from "../helpers/full-auto-state";

/** While the run waits, its ask is read again this often (the run reads an answer within 2 s). */
export const WAIT_POLL_MS = 3000;

/**
 * The apply agent's ask of one application (A25), from its detail (the cache the job page's Apply
 * section shares), read again while the run waits. Once the run moved on (an answer read, the
 * browser given back, the time up, the run's end) the posting's application and the queue are
 * read again, so the card follows it without a reload. `answer` sends the person's words to the
 * waiting run; `answerError` says why one was not taken (409 once the wait had ended).
 */
export function useAgentWait(application: Application) {
  const client = useQueryClient();
  const { id, posting_id: postingId } = application;
  const detail = useQuery({
    queryKey: applicationKey(id),
    queryFn: ({ signal }) => getApplication(id, signal),
    enabled: showsAsk(application) || application.state === "submit_unknown",
    refetchInterval: (current) =>
      agentWaits(current.state.data ?? application) ? WAIT_POLL_MS : false,
  });
  const read = detail.data;
  const moved =
    read !== undefined &&
    (read.state !== application.state || read.needs !== application.needs);
  useEffect(() => {
    if (!moved) return;
    void client.invalidateQueries({ queryKey: applicationsKey(postingId) });
    void client.invalidateQueries({ queryKey: queueKey });
  }, [moved, client, postingId]);

  const answer = useMutation({
    mutationFn: (text: string) => answerQuestion(id, text, false),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: applicationKey(id) });
    },
  });
  const error = answer.error;
  return {
    detail,
    answer,
    answerError:
      error === null
        ? null
        : isApiError(error) && error.status === 409
          ? LATE_ANSWER
          : describeError(error),
    offset: serverOffset(read?.now, detail.dataUpdatedAt),
  };
}
