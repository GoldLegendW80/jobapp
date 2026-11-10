/**
 * The chat of the notes on an offer letter: the documents chat (its messages, its generation
 * followed until it ends, Stop), in a sheet opened from the notes. The api answers what to
 * settle when the model credential is missing; no consent is asked.
 */
import { MessageSquare } from "lucide-react";
import { useRef, useState } from "react";

import { ChatEmptyState } from "@/components/shared/agents/chat/chat-empty-state";
import { ChatPrompt } from "@/components/shared/agents/chat/chat-prompt";
import { ChatThread } from "@/components/shared/agents/chat/chat-thread";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import { failureSentence } from "@/features/documents/api/documents-api";
import {
  useGeneration,
  useMessages,
  useSendMessage,
  useStopGeneration,
} from "@/features/documents/api/use-chat";
import { useRefreshOfferNotes } from "@/features/documents/api/use-offer-notes";
import {
  drawn,
  pendingGeneration,
} from "@/features/documents/helpers/chat-text";
import { isOpen } from "@/features/documents/helpers/labels";
import {
  CHAT_EXAMPLES,
  CHAT_INTRO,
} from "@/features/documents/helpers/offer-notes";
import { CHAT_MAX_CHARS } from "../chat-panel";
import { ChatMessages } from "../chat-panel/chat-messages";

const TITLE = "Ask about the notes";

export function OfferNotesChat({
  jobId,
  documentId,
  reading,
}: {
  jobId: string;
  documentId: string;
  /** The letter is being read: nothing can be asked until the notes are stored. */
  reading: boolean;
}) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm">
          <MessageSquare aria-hidden="true" />
          {TITLE}
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{TITLE}</SheetTitle>
          <SheetDescription>
            Each change is a new version of the notes.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 overflow-y-auto px-4 pb-6">
          <Conversation
            jobId={jobId}
            documentId={documentId}
            reading={reading}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Conversation({
  jobId,
  documentId,
  reading,
}: {
  jobId: string;
  documentId: string;
  reading: boolean;
}) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const messages = useMessages(documentId);
  const shown = messages.data ?? [];
  const pending = pendingGeneration(shown);
  const followed = useGeneration(pending);
  useRefreshOfferNotes(
    jobId,
    pending !== null && followed.data?.id === pending && !isOpen(followed.data),
  );
  const send = useSendMessage(documentId, {
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
  const stop = useStopGeneration(documentId, {
    onStart: () => {
      setError(null);
    },
    onError: (failure) => {
      setError(failureSentence(failure));
    },
  });
  const text = draft.trim();
  const busy = pending !== null || send.isPending;
  const canSend = messages.isSuccess && !reading && !busy && drawn(text);
  return (
    <div className="flex flex-col gap-3" data-testid="offer-notes-chat">
      <ChatThread
        fit
        scrollTestId="offer-notes-chat-scroll"
        loading={messages.isPending}
        error={messages.isError ? failureSentence(messages.error) : undefined}
        empty={
          shown.length === 0 ? (
            <ChatEmptyState
              testId="offer-notes-chat-empty"
              description={CHAT_INTRO}
              examples={CHAT_EXAMPLES}
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
                    label: "Changing the notes",
                    stopping: stop.isPending,
                    onStop: () => {
                      stop.mutate(pending);
                    },
                  }
            }
          />
        ) : null}
      </ChatThread>
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
        hint={
          reading
            ? "The letter is being read. You can ask once the notes are stored."
            : busy
              ? "One message at a time: you can send the next one once this one is answered."
              : undefined
        }
        placeholder="Ask about a clause"
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
