import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { describeError, isApiError } from "@/lib/api";

import {
  answerQuestion,
  applicationKey,
  applicationsKey,
  getApplication,
  giveCode,
  OnceCode,
  sendMessage,
  type Application,
  type ApplicationDetail,
} from "./apply-api";
import { queueKey } from "./queue-api";
import {
  agentWaits,
  assistantWorks,
  showsAsk,
  unread,
} from "../helpers/agent-wait";
import { serverOffset } from "../helpers/full-auto-state";

/** While the run waits, its ask is read again this often (the run looks for an answer every 2 s,
 * api applier.ASK_POLL_S); as often while a message of the person is not read yet. */
export const WAIT_POLL_MS = 3000;

const errorOf = (error: Error | null) =>
  error === null ? null : describeError(error);

/** The api refused a message because no run works on the application now (409). */
export const messageClosed = (error: unknown) =>
  isApiError(error) && error.status === 409;

/**
 * The apply agent's ask of one application (A25), from its detail (the cache the job page's Apply
 * section shares), read again while the run waits, and while the agent `works` with a message of
 * the person not read yet. Once the run moved on (an answer read, the browser given back, the
 * time up, the run's end) the posting's application and the queue are read again, so the card
 * follows it without a reload. `answer` sends the person's words to the waiting run, `give` a
 * code (kept nowhere: the mutation holds an emptied `OnceCode`), `message` the person's words to
 * the agent at work; each error is the api's own sentence, which never repeats what was sent.
 */
export function useAgentWait(application: Application, works = false) {
  const client = useQueryClient();
  const { id, posting_id: postingId } = application;
  const detail = useQuery({
    queryKey: applicationKey(id),
    queryFn: ({ signal }) => getApplication(id, signal),
    enabled:
      showsAsk(application) || application.state === "submit_unknown" || works,
    refetchInterval: (current) => {
      const data = current.state.data;
      if (agentWaits(data ?? application)) return WAIT_POLL_MS;
      return data &&
        assistantWorks(data, data.review_agent === true) &&
        unread(data.chat)
        ? WAIT_POLL_MS
        : false;
    },
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

  const readAgain = () => {
    void client.invalidateQueries({ queryKey: applicationKey(id) });
  };
  // gcTime 0: a mutation's variables (words that may hold a code, the emptied code holder) leave
  // the cache with the component that sent them.
  const answer = useMutation({
    mutationFn: (text: string) => answerQuestion(id, text, false),
    gcTime: 0,
    onSettled: readAgain,
  });
  const code = useMutation({
    mutationFn: (given: OnceCode) => giveCode(id, given),
    gcTime: 0,
    onSettled: readAgain,
  });
  const message = useMutation({
    mutationFn: (text: string) => sendMessage(id, text),
    gcTime: 0,
    onSuccess: (line) => {
      client.setQueryData<ApplicationDetail>(applicationKey(id), (old) =>
        old ? { ...old, chat: [...(old.chat ?? []), line] } : old,
      );
    },
    onSettled: readAgain,
  });
  return {
    detail,
    answer,
    answerError: errorOf(answer.error),
    /** Sends `typed` as the code of the open ask; the caller empties its box first. */
    give: (typed: string) => {
      code.mutate(new OnceCode(typed));
    },
    giving: code.isPending,
    codeError: errorOf(code.error),
    message,
    // A 409: no run works on it now, so the box goes at the next read and the caller says it in
    // a toast (`messageClosed`), which outlives the box.
    messageError: messageClosed(message.error) ? null : errorOf(message.error),
    offset: serverOffset(read?.now, detail.dataUpdatedAt),
  };
}
