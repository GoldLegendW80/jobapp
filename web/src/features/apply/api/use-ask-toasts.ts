import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { claimRun } from "@/features/agents/helpers/run-ends";

import { applicationKey, getApplication } from "./apply-api";
import { getQueue, queueListKey } from "./queue-api";
import { useNeedsCount } from "./use-queue";
import { agentWaits, isAsk } from "../helpers/agent-wait";

/** The asks announced in this browser (A25), apart from the runs' ends of A12. */
export const ASKS_KEY = "apply:announced-asks";

/** One ask of the apply agent to announce: the posting it applies to, and where its card is. */
export interface AskToast {
  /** The application and the end of its wait: one per ask. */
  key: string;
  title: string;
  company: string;
  postingId: string;
}

/**
 * Calls `onAsk` once for each ask of the apply agent that waits for the person (A25), on every page
 * of the signed in shell, including one that waits when the page opens. Read from the shell's
 * count of the applications that need the person (no interval of its own): when that count rises,
 * the queue is read once and each application waiting on the assistant's ask is read for its
 * deadline, which names the ask. An ask another tab of this browser announced is left out. A count
 * that stays the same while one wait ends and another begins between two reads is not seen until
 * the next change (declared in the pull request).
 */
export function useAskToasts(onAsk: (ask: AskToast) => void) {
  const client = useQueryClient();
  const { data, dataUpdatedAt } = useNeedsCount();
  const last = useRef<number | null>(null);
  const announce = useRef(onAsk);
  useEffect(() => {
    announce.current = onAsk;
  }, [onAsk]);

  useEffect(() => {
    if (data === undefined) return;
    const previous = last.current;
    last.current = data;
    if (data <= 0 || (previous !== null && data <= previous)) return;
    const read = async () => {
      const queue = await client.query({
        queryKey: queueListKey,
        queryFn: ({ signal }) => getQueue(signal),
        staleTime: 0,
      });
      for (const item of queue.items.filter(agentWaits)) {
        const detail = await client.query({
          queryKey: applicationKey(item.id),
          queryFn: ({ signal }) => getApplication(item.id, signal),
          staleTime: 0,
        });
        const question = detail.question;
        if (!agentWaits(detail) || !isAsk(question) || question?.live !== true)
          continue;
        const key = `${item.id}:${question.until ?? ""}`;
        if (!claimRun(key, undefined, ASKS_KEY)) continue;
        announce.current({
          key,
          title: item.title,
          company: item.company,
          postingId: item.posting_id,
        });
      }
    };
    read().catch(() => {
      // A read that failed: the next count tries again.
      last.current = previous;
    });
  }, [client, data, dataUpdatedAt]);
}
