import type { ChatMessage as Message } from "@/features/agents/api/chat-api";
import { ChatMessage } from "./chat-message";
import { LiveReply } from "./live-reply";

/** The messages of a conversation, oldest first, and the answer being written last. */
export function ChatMessageList({
  messages,
  truncated,
  activeRun,
  onAnswerEnd,
  onAnswerGrow,
}: {
  messages: Message[];
  /** The server sent the newest messages only. */
  truncated: boolean;
  activeRun: string | null;
  onAnswerEnd: () => void;
  onAnswerGrow: () => void;
}) {
  return (
    <>
      {truncated ? (
        <p className="pb-3 text-center text-xs text-muted-foreground">
          Older messages are not shown.
        </p>
      ) : null}
      {/* pb-6: room for the "Writing" line under the answer being written, kept when
          it goes so the list does not shift. */}
      <ol aria-label="Messages" className="flex flex-col gap-4 pb-6">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} messages={messages} />
        ))}
        {activeRun ? (
          <LiveReply
            key={activeRun}
            runId={activeRun}
            onEnd={onAnswerEnd}
            onGrow={onAnswerGrow}
          />
        ) : null}
      </ol>
    </>
  );
}
