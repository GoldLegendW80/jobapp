/** Sending a message of a job's chat: opens the job's conversation on the first message, then
 * shows the message and the run answering it before the thread is read again. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  busyRunId,
  jobThreadKey,
  openJobThread,
  sendMessage,
  threadKey,
  type Thread,
  type ThreadDetail,
} from "./chat-api";
import { runsKey } from "./runs-api";

interface Options {
  jobId: string;
  /** The job's conversation, null until a first message opens one. */
  thread: Thread | null;
  /** The message is stored: clear the composer and show it. */
  onSent: () => void;
  /** A held message stops waiting: it is on its way, or its conversation is gone. */
  dropHeld: () => void;
  refresh: () => void;
}

export function useSendChatMessage({
  jobId,
  thread,
  onSent,
  dropHeld,
  refresh,
}: Options) {
  const client = useQueryClient();
  return useMutation({
    onMutate: () => {
      dropHeld(); // whatever was held is on its way now
    },
    mutationFn: async (text: string) => {
      let target = thread;
      if (target === null) {
        target = await openJobThread(jobId);
        // A read of the job's conversation may be in flight, started before it existed (a
        // refetch on focus). Its answer, "none", is older than this one: drop it, or the
        // panel would show the empty state while the answer runs.
        await client.cancelQueries({
          queryKey: jobThreadKey(jobId),
          exact: true,
        });
        client.setQueryData(jobThreadKey(jobId), target);
      }
      return { target, sent: await sendMessage(target.id, text) };
    },
    onSuccess: async ({ target, sent }) => {
      onSent();
      const id = target.id;
      // A read of the thread may be in flight, started before the message was stored (on the
      // first message it starts as soon as the thread exists, next to the send). Its answer is
      // older than the send: drop it, so it can neither hide the message nor be the only read.
      await client.cancelQueries({ queryKey: threadKey(id) });
      client.setQueryData<ThreadDetail>(threadKey(id), (old) => {
        const known = old ?? {
          ...target,
          messages: [],
          messages_truncated: false,
          active_run_id: null,
        };
        return {
          ...known,
          messages: [
            ...known.messages.filter((m) => m.id !== sent.message.id),
            sent.message,
          ],
          active_run_id: sent.run.id,
        };
      });
      void client.invalidateQueries({ queryKey: threadKey(id) });
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    onError: (error) => {
      if (busyRunId(error)) {
        toast.info("The previous message is still being answered.");
      } else if (isApiError(error) && error.status === 404) {
        // Deleted somewhere else between the read and the send. The draft is kept, and with
        // the empty state back nothing is sent again without a new Enter.
        dropHeld();
        toast.info(
          "This conversation was deleted. Send again to start a new one.",
        );
        void client.invalidateQueries({
          queryKey: jobThreadKey(jobId),
          exact: true,
        });
      } else {
        toast.error(
          `The message was not sent. ${errorDetail(error) ?? describeError(error)}`,
        );
      }
      refresh();
    },
  });
}
