import { CircleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

import type { ChatMessage as Message } from "@/features/agents/api/chat-api";
import { unanswered } from "@/features/agents/helpers/chat-messages";
import { ChatBubble } from "./chat-bubble";

/** A stored message: the reader's on the right, the agent's on the left, and why a message of
 * the reader got no answer. */
export function ChatMessage({
  message,
  messages,
}: {
  message: Message;
  messages: Message[];
}) {
  const mine = message.role === "user";
  const failed = unanswered(message, messages);
  return (
    <li
      data-testid="chat-message"
      data-role={message.role}
      className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}
    >
      <span className="sr-only">{mine ? "You:" : "Agent:"}</span>
      <ChatBubble from={mine ? "user" : "agent"}>
        {message.text || (
          <span className="text-muted-foreground">The agent gave no text.</span>
        )}
      </ChatBubble>
      {failed ? (
        <p className="inline-flex items-center gap-1 text-xs text-destructive">
          <CircleAlert aria-hidden="true" className="size-3.5" />
          No answer:{" "}
          {message.error ??
            (message.run_status === "cancelled"
              ? "the answer was stopped."
              : "the agent failed.")}
        </p>
      ) : null}
    </li>
  );
}
