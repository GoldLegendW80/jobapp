import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Spinner } from "@/components/ui/spinner";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { describeError } from "@/lib/api";

import { useModelCredential } from "@/features/agents/api/use-model-credential";
import { useAnswerAnnouncement } from "@/features/agents/hooks/use-answer-announcement";
import { useHeldMessage } from "@/features/agents/hooks/use-held-message";
import { useJobThread } from "@/features/agents/api/use-job-thread";
import { useSendChatMessage } from "@/features/agents/api/use-send-chat-message";
import { useStickToBottom } from "@/features/agents/hooks/use-stick-to-bottom";
import { ChatComposer } from "./chat-composer";
import { ChatEmptyState } from "./chat-empty-state";
import { ChatMessageList } from "./chat-message-list";
import { DeleteConversation } from "./delete-conversation";
import { HeldMessage } from "./held-message";

/** The body of the chat panel: the conversation of one job and the composer under it. */
export function ChatConversation({ job }: { job: JobDetail }) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const { held, hold, drop } = useHeldMessage(job.id);
  const { follow } = useStickToBottom(list);

  const credential = useModelCredential();
  const conversation = useJobThread(job.id);
  const { threadId, detail, messages, activeRun, loading, refresh } =
    conversation;
  const { announcement, forget } = useAnswerAnnouncement(detail);

  // Keep the newest message in view, for a reader who is at the bottom (always after sending:
  // `follow(true)` below). Someone reading further up is left there.
  const liveSteps = activeRun ?? "";
  useLayoutEffect(() => {
    follow();
  }, [follow, messages.length, liveSteps, held]);

  const send = useSendChatMessage({
    jobId: job.id,
    thread: conversation.thread,
    dropHeld: drop,
    refresh,
    onSent: () => {
      setDraft("");
      drop();
      follow(true); // the reader just sent: show the message and the answer to come
    },
  });

  const noCredential = credential.isSuccess && credential.data === null;
  const busy = activeRun !== null || send.isPending;
  const text = draft.trim();
  const canSend = !busy && !noCredential && !loading && text.length > 0;

  const waiting = busy || loading;
  const submit = () => {
    if (canSend) {
      send.mutate(text);
    } else if (waiting && !send.isPending && !noCredential && text.length > 0) {
      // Never dropped: the composer is enabled in these states, so Enter has to mean
      // something. The draft stays where it is and is sent when the wait is over. Not while
      // a send is in flight: that Enter repeats a message already on its way.
      hold(text);
    }
  };
  const sendHeld = held !== null && held === text && canSend;
  const { mutate: sendNow } = send;
  useEffect(() => {
    if (sendHeld) sendNow(text);
  }, [sendHeld, sendNow, text]);
  const holding = held !== null && !noCredential;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* The new answer only, once: the list itself is not live. */}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {threadId ? (
        <DeleteConversation
          jobId={job.id}
          threadId={threadId}
          onDeleted={forget}
        />
      ) : null}
      <div
        ref={list}
        data-testid="chat-scroll"
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
      >
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner aria-hidden="true" role={undefined} />
            Loading the conversation
          </p>
        ) : conversation.failedToLoad ? (
          <p className="text-sm text-destructive">
            The conversation could not be loaded.{" "}
            {describeError(conversation.loadError)}
          </p>
        ) : messages.length === 0 && !busy ? (
          <ChatEmptyState
            onPick={(prompt) => {
              setDraft(prompt);
              drop();
              composer.current?.focus();
            }}
          />
        ) : (
          <ChatMessageList
            messages={messages}
            truncated={detail?.messages_truncated ?? false}
            activeRun={activeRun}
            onAnswerEnd={refresh}
            onAnswerGrow={follow}
          />
        )}
        {holding ? (
          <HeldMessage
            text={held}
            onCancel={() => {
              drop();
              composer.current?.focus();
            }}
          />
        ) : null}
      </div>
      <ChatComposer
        composerRef={composer}
        draft={draft}
        noCredential={noCredential}
        canSend={canSend}
        sending={send.isPending}
        answering={busy}
        holding={holding}
        onDraftChange={(value) => {
          setDraft(value);
          // Clearing cancels, and so does any edit: what is in the composer is no longer
          // the text Enter was pressed on, so it is sent when its author says so.
          drop();
        }}
        onSubmit={submit}
      />
    </div>
  );
}
