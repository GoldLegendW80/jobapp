import { CircleAlert } from "lucide-react";

import type { ChatMessage as Message } from "@/features/agents/api/chat-api";
import { unanswered } from "@/features/agents/helpers/chat-messages";
import { ChatTurn } from "./chat-turn";

/** A stored message: the reader's on the right, the agent's on the left, and why a message of
 * the reader got no answer. */
export function ChatMessage({
  message,
  messages,
}: {
  message: Message;
  messages: Message[];
}) {
  const failed = unanswered(message, messages);
  return (
    <ChatTurn
      from={message.role === "user" ? "user" : "agent"}
      data-testid="chat-message"
      data-role={message.role}
      footer={
        failed ? (
          <span className="inline-flex items-center gap-1 text-destructive">
            <CircleAlert aria-hidden="true" className="size-3.5" />
            No answer:{" "}
            {message.error ??
              (message.run_status === "cancelled"
                ? "the answer was stopped."
                : "the agent failed.")}
          </span>
        ) : null
      }
    >
      {message.text || (
        <span className="text-muted-foreground">The agent gave no text.</span>
      )}
    </ChatTurn>
  );
}
