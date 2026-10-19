/**
 * The Chat view of a document (documents D17): the document's conversation and a box to ask for a
 * change. A message starts a generation of the chat writer, a private run whose text is never
 * streamed, so the panel polls the generation (`getGeneration`) until it ends, then reads the
 * conversation, the document and its versions again: the answer the app wrote names what changed,
 * what the fact check corrected and the version number. Bubbles and the list that follows the
 * newest message are the agents chat's (`useStickToBottom`); text is plain, never markdown.
 *
 * Nothing is sent before the consent of the current text and a model credential: the panel says
 * which is missing and the composer waits. One message at a time: while one is answered, or while
 * the document itself is being written, Send is disabled (the api would answer 409).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, SendHorizontal } from "lucide-react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useStickToBottom } from "@/features/agents/use-stick-to-bottom";
import { cn } from "@/lib/utils";

import { ConsentDialog } from "./consent-dialog";
import type { PanelProps } from "./document-view";
import {
  acceptConsent,
  consentKey,
  documentKey,
  failureSentence,
  generationKey,
  getConsent,
  getGeneration,
  listMessages,
  messagesKey,
  sendMessage,
  type Message,
} from "./documents-api";
import { isOpen, POLL_MS } from "./labels";

export const available = true;

/** The api's DOCUMENTS_CHAT_MAX_CHARS. */
export const CHAT_MAX_CHARS = 2000;

const EXAMPLES = [
  "Make the summary shorter",
  "Use the sidebar template",
  "Put my most relevant experience first",
];

const BUBBLE =
  "max-w-[85%] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap";

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

function MessageItem({ message }: { message: Message }) {
  const mine = message.role === "user";
  return (
    <li
      data-testid="document-chat-message"
      data-role={message.role}
      className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}
    >
      <span className="sr-only">{mine ? "You:" : "Assistant:"}</span>
      <div
        className={
          mine
            ? cn(BUBBLE, "rounded-br-sm bg-primary text-primary-foreground")
            : cn(BUBBLE, "rounded-bl-sm bg-muted")
        }
      >
        {message.text}
      </div>
      {message.version_number !== null && !mine ? (
        <span className="text-xs text-muted-foreground">
          Version {message.version_number}
        </span>
      ) : null}
    </li>
  );
}

export function ChatPanel({ document, generation }: PanelProps) {
  const client = useQueryClient();
  const ids = { composer: useId(), hint: useId() };
  const composer = useRef<HTMLTextAreaElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const { follow } = useStickToBottom(list);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [consenting, setConsenting] = useState(false);

  const messages = useQuery({
    queryKey: messagesKey(document.id),
    queryFn: ({ signal }) => listMessages(document.id, "content", signal),
  });
  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });
  const shown = messages.data ?? [];
  const pending = pendingGeneration(shown);
  const followed = useQuery({
    queryKey: generationKey(pending ?? "none"),
    queryFn: ({ signal }) => getGeneration(pending ?? "", signal),
    enabled: pending !== null,
    refetchInterval: (current) =>
      current.state.data === undefined || isOpen(current.state.data)
        ? POLL_MS
        : false,
  });

  // Ended: the answer, the new draft and the new version are read once.
  const ended =
    pending !== null &&
    followed.data !== undefined &&
    followed.data.id === pending &&
    !isOpen(followed.data);
  useEffect(() => {
    if (ended)
      void client.invalidateQueries({ queryKey: documentKey(document.id) });
  }, [ended, client, document.id]);

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

  useLayoutEffect(() => {
    follow();
  }, [follow, shown.length, pending]);

  const agree = useMutation({
    mutationFn: async (version: number) => {
      const accepted = await acceptConsent(version);
      client.setQueryData(consentKey, accepted);
      return accepted;
    },
    onSuccess: () => {
      setConsenting(false);
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });

  const send = useMutation({
    mutationFn: (text: string) => sendMessage(document.id, text),
    onMutate: () => {
      setError(null);
    },
    onSuccess: (sent) => {
      setDraft("");
      client.setQueryData<Message[]>(messagesKey(document.id), (old) => [
        ...(old ?? []).filter((one) => one.id !== sent.message.id),
        sent.message,
      ]);
      follow(true);
    },
    onError: (failure) => {
      setError(failureSentence(failure));
      void client.invalidateQueries({ queryKey: messagesKey(document.id) });
      void client.invalidateQueries({ queryKey: consentKey });
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
    text.length > 0;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (canSend) send.mutate(text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  const hint = writing
    ? "This document is being written. You can ask for a change once it is done."
    : busy
      ? "One message at a time: you can send the next one once this one is answered."
      : "Enter sends, Shift and Enter adds a line.";

  return (
    <div className="flex flex-col gap-3" data-testid="document-chat">
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      <div
        ref={list}
        data-testid="document-chat-scroll"
        className="max-h-[60vh] min-h-40 overflow-y-auto rounded-md border p-4"
      >
        {messages.isPending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading the conversation
          </p>
        ) : messages.isError ? (
          <p className="text-sm text-destructive">
            The conversation could not be loaded.{" "}
            {failureSentence(messages.error)}
          </p>
        ) : shown.length === 0 ? (
          <div
            className="flex flex-col gap-3"
            data-testid="document-chat-empty"
          >
            <p className="text-sm text-muted-foreground">
              Ask for a change to this document. The assistant changes what you
              ask and nothing else; the fact check removes any claim your
              profile does not state, and each change is a new version you can
              restore.
            </p>
            <ul aria-label="Examples" className="flex flex-col gap-2">
              {EXAMPLES.map((example) => (
                <li key={example}>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-auto w-full justify-start py-2 text-left whitespace-normal"
                    onClick={() => {
                      setDraft(example);
                      composer.current?.focus();
                    }}
                  >
                    {example}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ol aria-label="Messages" className="flex flex-col gap-4">
            {shown.map((message) => (
              <MessageItem key={message.id} message={message} />
            ))}
            {pending !== null ? (
              <li
                className="flex items-center gap-2 text-sm text-muted-foreground"
                data-testid="document-chat-writing"
              >
                <LoaderCircle
                  aria-hidden="true"
                  className="size-4 animate-spin motion-reduce:animate-none"
                />
                {followed.data?.status === "queued"
                  ? "Waiting for a free assistant"
                  : "Changing your document"}
              </li>
            ) : null}
          </ol>
        )}
      </div>

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

      <form
        onSubmit={submit}
        className="flex flex-col gap-2"
        aria-label="Send a message"
      >
        <label htmlFor={ids.composer} className="sr-only">
          Message
        </label>
        <div className="flex items-end gap-2">
          <Textarea
            id={ids.composer}
            ref={composer}
            value={draft}
            maxLength={CHAT_MAX_CHARS}
            rows={2}
            placeholder="Ask for a change"
            className="max-h-40 min-h-11 resize-none"
            aria-describedby={ids.hint}
            disabled={noConsent || noCredential}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            onKeyDown={onKeyDown}
          />
          <Button
            type="submit"
            size="icon"
            className="size-11 shrink-0"
            disabled={!canSend}
            aria-busy={send.isPending}
          >
            {send.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <SendHorizontal aria-hidden="true" />
            )}
            <span className="sr-only">Send</span>
          </Button>
        </div>
        <p
          id={ids.hint}
          className="flex justify-between gap-2 text-xs text-muted-foreground"
        >
          <span>{hint}</span>
          <span>
            {draft.length} of {CHAT_MAX_CHARS}
          </span>
        </p>
      </form>

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
