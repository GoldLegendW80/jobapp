import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";
import { Button } from "@/components/ui/button";
import { MessageScrollerItem } from "@/components/ui/message-scroller";
import { Spinner } from "@/components/ui/spinner";

import type { Message } from "@/features/documents/api/chat-api";

/** The answer being written under the newest message, and Stop. */
export interface PendingAnswer {
  label: string;
  stopping: boolean;
  onStop: () => void;
}

/**
 * A document's messages, the items of a ChatThread: the person's on the right and anchoring the
 * thread, the assistant's on the left with the version each made, then the answer being written
 * with Stop.
 */
export function ChatMessages({
  messages,
  pending,
}: {
  messages: Message[];
  pending: PendingAnswer | null;
}) {
  return (
    <>
      {messages.map((message) => (
        <MessageScrollerItem
          key={message.id}
          messageId={String(message.id)}
          scrollAnchor={message.role === "user"}
          data-testid="document-chat-message"
          data-role={message.role}
        >
          {message.role === "user" ? (
            <ChatTurn from="user">{message.text}</ChatTurn>
          ) : (
            <ChatTurn
              from="agent"
              label="Assistant:"
              footer={
                message.version_number === null
                  ? null
                  : `Version ${String(message.version_number)}`
              }
            >
              {message.text}
            </ChatTurn>
          )}
        </MessageScrollerItem>
      ))}
      {pending === null ? null : (
        <MessageScrollerItem
          messageId="pending"
          data-testid="document-chat-writing"
        >
          <ChatTurn
            from="agent"
            label="The assistant is answering:"
            footer={
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={pending.stopping}
                onClick={pending.onStop}
              >
                Stop
              </Button>
            }
          >
            <span className="flex items-center gap-2 text-muted-foreground">
              <Spinner
                aria-hidden="true"
                role={undefined}
                className="motion-reduce:animate-none"
              />
              {pending.label}
            </span>
          </ChatTurn>
        </MessageScrollerItem>
      )}
    </>
  );
}
