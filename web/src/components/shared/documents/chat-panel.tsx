/**
 * The chat of a document (documents D17): the document's conversation and a box to ask for a
 * change. The document view shows it in its side rail, or in a sheet on a phone
 * (document-view/chat-section.tsx). A message starts a generation of the chat writer, a private
 * run whose text is never streamed, so the panel polls the generation (`getGeneration`) until it ends, then reads the
 * conversation, the document and its versions again: the answer the app wrote names what changed,
 * what the fact check corrected and the version number. The thread, its turns and the prompt are
 * the posting's chat's own (shared/agents/chat); text is plain, never markdown.
 *
 * Nothing is sent before the consent of the current text and a model credential: the panel says
 * which is missing and the composer waits. One message at a time: while one is answered, or while
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
import { Button } from "@/components/ui/button";

import { drawn } from "@/features/documents/helpers/chat-text";
import { ChatMessages } from "./chat-panel/chat-messages";
import { ConsentDialog } from "./consent-dialog";
import type { PanelProps } from "./document-view";
import { failureSentence } from "@/features/documents/api/documents-api";
import { type Message } from "@/features/documents/api/chat-api";
import {
  useGeneration,
  useMessages,
  useRefreshWhenAnswered,
  useSendMessage,
  useStopGeneration,
} from "@/features/documents/api/use-chat";
import {
  useAcceptConsent,
  useConsent,
} from "@/features/documents/api/use-consent";
import { isOpen } from "@/features/documents/helpers/labels";

/** The api's DOCUMENTS_CHAT_MAX_CHARS. */
export const CHAT_MAX_CHARS = 2000;

const EXAMPLES = [
  "Make the summary shorter",
  "Use the sidebar template",
  "Put my most relevant experience first",
];

/** The generation of the newest message of the person while no answer names it. */
function pendingGeneration(messages: Message[]): string | null {
  const last = [...messages].reverse().find((one) => one.role === "user");
  if (!last?.generation_id) return null;
  const answered = messages.some(
    (one) =>
      one.role === "assistant" && one.generation_id === last.generation_id,
  );
  return answered ? null : last.generation_id;
}

export function ChatPanel({ document, generation }: PanelProps) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [consenting, setConsenting] = useState(false);

  const messages = useMessages(document.id);
  const consent = useConsent();
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

  const agree = useAcceptConsent({
    onAccepted: () => {
      setConsenting(false);
    },
  });

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

  const noConsent = consent.data !== undefined && !consent.data.accepted;
  const noCredential =
    consent.data !== undefined && consent.data.model === null;
  const writing = isOpen(generation);
  const busy = pending !== null || send.isPending;
  const text = draft.trim();
  const canSend =
    messages.isSuccess &&
    consent.isSuccess &&
    !noConsent &&
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

      {noConsent ? (
        <Alert>
          <AlertDescription className="flex flex-col items-start gap-2">
            The chat sends this document, your profile and the conversation to
            the model. Confirm what is sent first.
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setConsenting(true);
              }}
            >
              Read and confirm
            </Button>
          </AlertDescription>
        </Alert>
      ) : noCredential ? (
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
        disabled={noConsent || noCredential}
        canSend={canSend}
        sending={send.isPending}
        onChange={setDraft}
        onSubmit={() => {
          if (canSend) send.mutate(text);
        }}
      />

      {consent.data !== undefined ? (
        <ConsentDialog
          consent={consent.data}
          open={consenting}
          busy={agree.isPending}
          error={agree.isError ? failureSentence(agree.error) : undefined}
          onAgree={(version) => {
            agree.mutate(version);
          }}
          onClose={() => {
            setConsenting(false);
          }}
        />
      ) : null}
    </div>
  );
}
