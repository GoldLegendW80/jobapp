/**
 * The chat of a document (documents D17): the document's conversation and a box to ask for a
 * change. The document view shows it in its side rail, or in a sheet on a phone
 * (document-view/chat-section.tsx). A message starts a generation of the chat writer, a private
 * run whose text is never streamed, so the panel polls the generation (`getGeneration`) until it ends, then reads the
 * conversation, the document and its versions again: the answer the app wrote names what changed,
 * what the fact check corrected and the version number. The thread, its turns and the prompt are
 * the posting's chat's own (shared/agents/chat); text is plain, never markdown.
 *
 * Nothing is sent before a model credential is saved: the panel says it is missing and the
 * composer waits. One message at a time: while one is answered, or while
 * the document itself is being written, Send is disabled (the api would answer 409). Stop ends the
 * answer being written (`cancelGeneration`): the document stays as it was. A message with no
 * character drawn (only blanks or zero width characters) is not sent, as the api refuses it.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { ChatEmptyState } from "@/components/shared/agents/chat/chat-empty-state";
import { ChatPrompt } from "@/components/shared/agents/chat/chat-prompt";
import { ChatThread } from "@/components/shared/agents/chat/chat-thread";
import { Alert, AlertDescription } from "@/components/ui/alert";

import {
  drawn,
  pendingGeneration,
} from "@/features/documents/helpers/chat-text";
import { ChatMessages } from "./chat-panel/chat-messages";
import { KeepRule } from "./chat-panel/keep-rule";
import type { PanelProps } from "./document-view";
import { failureSentence } from "@/features/documents/api/documents-api";
import {
  useGeneration,
  useMessages,
  useRefreshWhenAnswered,
  useSendMessage,
  useStopGeneration,
} from "@/features/documents/api/use-chat";
import { useModelCredential } from "@/features/agents/api/use-model-credential";
import { isOpen } from "@/features/documents/helpers/labels";

/** The api's DOCUMENTS_CHAT_MAX_CHARS. */
export const CHAT_MAX_CHARS = 2000;

const EXAMPLES = [
  "Make the summary shorter",
  "Use the sidebar template",
  "Put my most relevant experience first",
];

export function ChatPanel({ document, generation }: PanelProps) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const messages = useMessages(document.id);
  const credential = useModelCredential();
  const shown = messages.data ?? [];
  const pending = pendingGeneration(shown);
  const followed = useGeneration(pending);

  // Ended: the answer, the new draft and the new version are read once.
  const ended =
    pending !== null &&
    followed.data !== undefined &&
    followed.data.id === pending &&
    !isOpen(followed.data);
  useRefreshWhenAnswered(document.id, ended);

  // Each new answer is announced once, never the ones already there when the view opened.
  const answers = shown.filter((one) => one.role === "assistant");
  const answerCount = answers.length;
  const lastAnswer = answers.at(-1)?.text ?? "";
  const seen = useRef<number | null>(null);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (!messages.isSuccess) return;
    if (seen.current !== null && answerCount > seen.current) {
      setAnnouncement(lastAnswer);
    }
    seen.current = answerCount;
  }, [answerCount, lastAnswer, messages.isSuccess]);

  const send = useSendMessage(document.id, {
    onStart: () => {
      setError(null);
    },
    onSent: () => {
      setDraft("");
    },
    onError: (failure) => {
      setError(failureSentence(failure));
    },
  });

  const stop = useStopGeneration(document.id, {
    onStart: () => {
      setError(null);
    },
    onError: (failure) => {
      setError(failureSentence(failure));
    },
  });

  const noCredential = credential.data === null;
  const writing = isOpen(generation);
  const busy = pending !== null || send.isPending;
  const text = draft.trim();
  const canSend =
    messages.isSuccess &&
    credential.isSuccess &&
    !noCredential &&
    !writing &&
    !busy &&
    drawn(text);

  const hint = writing
    ? "This document is being written. You can ask for a change once it is done."
    : busy
      ? "One message at a time: you can send the next one once this one is answered."
      : undefined;

  return (
    <div className="flex flex-col gap-3" data-testid="document-chat">
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      <ChatThread
        fit
        scrollTestId="document-chat-scroll"
        loading={messages.isPending}
        error={messages.isError ? failureSentence(messages.error) : undefined}
        empty={
          shown.length === 0 ? (
            <ChatEmptyState
              testId="document-chat-empty"
              description="The assistant changes what you ask and nothing else; the fact check removes any claim your profile does not state."
              examples={EXAMPLES}
              examplesLabel="Examples"
              onPick={(example) => {
                setDraft(example);
                composer.current?.focus();
              }}
            />
          ) : null
        }
      >
        {messages.isSuccess ? (
          <ChatMessages
            messages={shown}
            pending={
              pending === null
                ? null
                : {
                    label:
                      followed.data?.status === "queued"
                        ? "Waiting for a free assistant"
                        : "Changing your document",
                    stopping: stop.isPending,
                    onStop: () => {
                      stop.mutate(pending);
                    },
                  }
            }
          />
        ) : null}
      </ChatThread>

      <KeepRule
        documentId={document.id}
        messages={shown}
        answering={pending !== null}
      />
      {noCredential ? (
        <Alert>
          <AlertDescription>
            The chat runs with your own model credential, and none is saved yet.{" "}
            <Link to="/account" className="underline underline-offset-4">
              Add one on the Account page
            </Link>
            .
          </AlertDescription>
        </Alert>
      ) : null}
      {error !== null ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <ChatPrompt
        textareaRef={composer}
        value={draft}
        max={CHAT_MAX_CHARS}
        showCount
        hint={hint}
        placeholder="Ask for a change"
        disabled={noCredential}
        canSend={canSend}
        sending={send.isPending}
        onChange={setDraft}
        onSubmit={() => {
          if (canSend) send.mutate(text);
        }}
      />
    </div>
  );
}
