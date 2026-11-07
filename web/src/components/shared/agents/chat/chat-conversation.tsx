import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Kbd } from "@/components/ui/kbd";
import { MessageScrollerItem } from "@/components/ui/message-scroller";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { describeError } from "@/lib/api";

import { MESSAGE_MAX } from "@/features/agents/api/chat-api";
import { useModelCredential } from "@/features/agents/api/use-model-credential";
import { useAnswerAnnouncement } from "@/features/agents/hooks/use-answer-announcement";
import { useHeldMessage } from "@/features/agents/hooks/use-held-message";
import { useJobThread } from "@/features/agents/api/use-job-thread";
import { useSendChatMessage } from "@/features/agents/api/use-send-chat-message";
import { ChatEmptyState } from "./chat-empty-state";
import { ChatMessageList } from "./chat-message-list";
import { ChatPrompt } from "./chat-prompt";
import { ChatThread } from "./chat-thread";
import { DeleteConversation } from "./delete-conversation";
import { HeldMessage } from "./held-message";

const EXAMPLE_QUESTIONS = [
  "Does this role fit my profile?",
  "What should I stress in my application?",
  "What should I check before applying?",
];

/** The body of the chat panel: the conversation of one job and the composer under it. */
export function ChatConversation({ job }: { job: JobDetail }) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const { held, hold, drop } = useHeldMessage(job.id);

  const credential = useModelCredential();
  const conversation = useJobThread(job.id);
  const { threadId, detail, messages, activeRun, loading, refresh } =
    conversation;
  const { announcement, forget } = useAnswerAnnouncement(detail);

  const send = useSendChatMessage({
    jobId: job.id,
    thread: conversation.thread,
    dropHeld: drop,
    refresh,
    onSent: () => {
      setDraft("");
      drop();
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
      {/* The new answer only, once: the thread itself is not live. */}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {threadId ? (
        <DeleteConversation
          jobId={job.id}
          threadId={threadId}
          onDeleted={forget}
          focusAfterDelete={composer}
        />
      ) : null}
      <ChatThread
        scrollTestId="chat-scroll"
        loading={loading}
        error={
          conversation.failedToLoad
            ? describeError(conversation.loadError)
            : undefined
        }
        empty={
          messages.length === 0 && !busy ? (
            <ChatEmptyState
              testId="chat-empty"
              title="Ask anything about this posting."
              description="The agent reads the posting and your profile to answer. It changes nothing, and it only knows what they say."
              examples={EXAMPLE_QUESTIONS}
              examplesLabel="Example questions"
              onPick={(prompt) => {
                setDraft(prompt);
                drop();
                composer.current?.focus();
              }}
            />
          ) : null
        }
      >
        {loading || conversation.failedToLoad ? null : (
          <ChatMessageList
            messages={messages}
            truncated={detail?.messages_truncated ?? false}
            activeRun={activeRun}
            onAnswerEnd={refresh}
          />
        )}
        {holding ? (
          <MessageScrollerItem messageId="held">
            <HeldMessage
              text={held}
              onCancel={() => {
                drop();
                composer.current?.focus();
              }}
            />
          </MessageScrollerItem>
        ) : null}
      </ChatThread>
      <div className="flex flex-col gap-2 border-t p-4">
        {noCredential ? (
          <p className="text-sm text-muted-foreground">
            Agents run with your own model credential, and none is saved yet.{" "}
            <Link to="/account" className="underline underline-offset-4">
              Add one on the Account page
            </Link>
            .
          </p>
        ) : null}
        <ChatPrompt
          textareaRef={composer}
          value={draft}
          max={MESSAGE_MAX}
          placeholder="Ask about this job"
          hint={
            holding ? (
              `Your message will be sent when ${
                busy ? "the answer is complete" : "the conversation has loaded"
              }. Edit or clear it to cancel.`
            ) : busy ? (
              <>
                <Kbd>Enter</Kbd> sends your message once the answer is complete.
              </>
            ) : undefined
          }
          disabled={noCredential}
          canSend={canSend}
          sending={send.isPending}
          onChange={(value) => {
            setDraft(value);
            // Clearing cancels, and so does any edit: what is in the composer is no longer
            // the text Enter was pressed on, so it is sent when its author says so.
            drop();
          }}
          onSubmit={submit}
        />
      </div>
    </div>
  );
}
