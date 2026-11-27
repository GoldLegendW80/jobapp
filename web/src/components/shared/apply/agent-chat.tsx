import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";

import type { ChatLine } from "@/features/apply/api/apply-api";
import {
  askEndLine,
  isMessage,
  kindWords,
  MESSAGE_SHOWN_MAX,
  NO_PASSWORD,
  readLine,
  shownText,
} from "@/features/apply/helpers/agent-wait";

/** Who said a line, under its bubble: the assistant's ask, the person's answer, code or message. */
function footerOf(line: ChatLine): string {
  if (line.who !== "person") {
    const end = askEndLine(line.end);
    return `Assistant, asking for ${kindWords(line.kind)}.${end ? ` ${end}` : ""}`;
  }
  const read = readLine(line);
  return read ? `You, to the assistant. ${read}` : "You.";
}

/**
 * The conversation with the apply agent of one application (A25): each ask of its runs, each
 * answer of the person and each message they wrote to it, in the api's order, with who said it
 * and whether the assistant read a message, then the standing line on passwords. The assistant's
 * lines are the model's own words, which a page can steer: plain text, one line, bounded, never a
 * link, markup or a button (`shownText`).
 */
export function AgentChat({ chat }: { chat: readonly ChatLine[] }) {
  return (
    <div className="flex flex-col gap-3" data-testid="agent-chat">
      {chat.length > 0 ? (
        <ol
          aria-label="Your conversation with the assistant"
          className="flex flex-col gap-3"
        >
          {chat.map((line, index) => {
            const assistant = line.who !== "person";
            return (
              <li key={`${String(index)}:${line.at ?? ""}`}>
                <ChatTurn
                  from={assistant ? "agent" : "user"}
                  label={assistant ? "The assistant said:" : "You said:"}
                  footer={<span>{footerOf(line)}</span>}
                >
                  <span className="[overflow-wrap:anywhere]">
                    {isMessage(line)
                      ? shownText(line.text, MESSAGE_SHOWN_MAX)
                      : shownText(line.text)}
                  </span>
                </ChatTurn>
              </li>
            );
          })}
        </ol>
      ) : null}
      <p className="text-xs text-muted-foreground">{NO_PASSWORD}</p>
    </div>
  );
}
