import { SendHorizontal } from "lucide-react";
import { useId, type KeyboardEvent, type RefObject } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

import { MESSAGE_MAX } from "@/features/agents/api/chat-api";

/** The form under the conversation: the message, Send, and a hint that says what Enter does. */
export function ChatComposer({
  composerRef,
  draft,
  noCredential,
  canSend,
  sending,
  answering,
  holding,
  onDraftChange,
  onSubmit,
}: {
  composerRef: RefObject<HTMLTextAreaElement>;
  draft: string;
  noCredential: boolean;
  canSend: boolean;
  sending: boolean;
  /** A message is being sent or answered. */
  answering: boolean;
  /** A message waits to be sent. */
  holding: boolean;
  onDraftChange: (text: string) => void;
  onSubmit: () => void;
}) {
  const ids = { composer: useId(), hint: useId() };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      onSubmit();
    }
  };
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      className="flex flex-col gap-2 border-t p-4"
      aria-label="Send a message"
    >
      {noCredential ? (
        <p className="text-sm text-muted-foreground">
          Agents run with your own model credential, and none is saved yet.{" "}
          <Link to="/account" className="underline underline-offset-4">
            Add one on the Account page
          </Link>
          .
        </p>
      ) : null}
      <Label htmlFor={ids.composer} className="sr-only">
        Message
      </Label>
      <div className="flex items-end gap-2">
        <Textarea
          id={ids.composer}
          ref={composerRef}
          value={draft}
          maxLength={MESSAGE_MAX}
          rows={2}
          placeholder="Ask about this job"
          className="max-h-40 min-h-11 resize-none"
          aria-describedby={ids.hint}
          disabled={noCredential}
          onChange={(event) => {
            onDraftChange(event.target.value);
          }}
          onKeyDown={onKeyDown}
        />
        <Button
          type="submit"
          size="icon"
          className="size-11 shrink-0"
          disabled={!canSend}
          aria-busy={sending}
        >
          {sending ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <SendHorizontal aria-hidden="true" />
          )}
          <span className="sr-only">Send</span>
        </Button>
      </div>
      <p
        id={ids.hint}
        className="text-xs text-muted-foreground"
        aria-live="polite"
        aria-atomic="true"
      >
        {holding ? (
          `Your message will be sent when ${
            answering ? "the answer is complete" : "the conversation has loaded"
          }. Edit or clear it to cancel.`
        ) : answering ? (
          <>
            <Kbd>Enter</Kbd> sends your message once the answer is complete.
          </>
        ) : (
          <>
            <Kbd>Enter</Kbd> sends, <Kbd>Shift</Kbd> and <Kbd>Enter</Kbd> adds a
            line.
          </>
        )}
      </p>
    </form>
  );
}
