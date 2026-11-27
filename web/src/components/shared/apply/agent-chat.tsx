import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";

import type { ChatLine } from "@/features/apply/api/apply-api";
import {
  askEndLine,
  kindWords,
  NO_PASSWORD,
  shownText,
} from "@/features/apply/helpers/agent-wait";

/**
 * The conversation with the apply agent of one application (A25): each ask of its runs and each
 * answer of the person, in order, with who said it, then the standing line on passwords. The
 * assistant's lines are the model's own words, which a page can steer: plain text, one line,
 * bounded, never a link, markup or a button (`shownText`).
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
            const end = assistant ? askEndLine(line.end) : null;
            return (
              <li key={`${String(index)}:${line.at ?? ""}`}>
                <ChatTurn
                  from={assistant ? "agent" : "user"}
                  label={assistant ? "The assistant said:" : "You said:"}
                  footer={
                    <span>
                      {assistant
                        ? `Assistant, asking for ${kindWords(line.kind)}.`
                        : "You."}
                      {end ? ` ${end}` : null}
                    </span>
                  }
                >
                  <span className="[overflow-wrap:anywhere]">
                    {shownText(line.text)}
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
