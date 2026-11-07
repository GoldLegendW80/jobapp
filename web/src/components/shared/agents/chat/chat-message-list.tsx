import { Marker, MarkerContent } from "@/components/ui/marker";
import { MessageScrollerItem } from "@/components/ui/message-scroller";

import type { ChatMessage as Message } from "@/features/agents/api/chat-api";
import { ChatMessage } from "./chat-message";
import { LiveReply } from "./live-reply";

/**
 * The messages of a conversation, oldest first, and the answer being written last: the items of
 * a ChatThread. The reader's messages anchor the thread, so the newest question stays in view
 * while it is answered.
 */
export function ChatMessageList({
  messages,
  truncated,
  activeRun,
  onAnswerEnd,
}: {
  messages: Message[];
  /** The server sent the newest messages only. */
  truncated: boolean;
  activeRun: string | null;
  onAnswerEnd: () => void;
}) {
  return (
    <>
      {truncated ? (
        <MessageScrollerItem>
          <Marker variant="separator" className="text-xs">
            <MarkerContent>Older messages are not shown.</MarkerContent>
          </Marker>
        </MessageScrollerItem>
      ) : null}
      {messages.map((message) => (
        <MessageScrollerItem
          key={message.id}
          messageId={String(message.id)}
          scrollAnchor={message.role === "user"}
        >
          <ChatMessage message={message} messages={messages} />
        </MessageScrollerItem>
      ))}
      {activeRun ? (
        // Empty once a stopped answer is dropped, until the conversation is read again.
        <MessageScrollerItem
          key={activeRun}
          messageId={`run-${activeRun}`}
          className="empty:hidden"
        >
          <LiveReply runId={activeRun} onEnd={onAnswerEnd} />
        </MessageScrollerItem>
      ) : null}
    </>
  );
}
