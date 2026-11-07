import { useEffect, useRef } from "react";

import { FINAL_STATUSES } from "@/features/agents/api/runs-api";
import { useRunStream } from "@/features/agents/api/use-run-stream";
import { replyActivity } from "@/features/agents/helpers/chat-messages";
import { lastText } from "@/features/agents/helpers/live-text";
import { ChatTurn } from "./chat-turn";

/**
 * The answer being written: its text as it grows (the run's live text), else the newest text the
 * run stored, else what the agent is doing. Once the run is done the final text stays, drawn as
 * a stored answer, until the thread's read brings the stored one and this unmounts: the answer
 * is never absent in between. A run that failed or was cancelled stores no answer, so its
 * unfinished text goes, and the message above says why there is none.
 */
export function LiveReply({
  runId,
  onEnd,
}: {
  runId: string;
  onEnd: () => void;
}) {
  const { steps, live, status } = useRunStream(runId);
  const ended = status !== undefined && FINAL_STATUSES.has(status);
  const endRef = useRef(onEnd);
  useEffect(() => {
    endRef.current = onEnd;
  });
  useEffect(() => {
    if (ended) endRef.current();
  }, [ended]);
  const stored = lastText(steps);
  const writing = live?.text ? live.text : undefined;
  const text = writing ?? stored;
  if (ended) {
    if (status !== "done" || stored === undefined) return null;
    return (
      <ChatTurn
        from="agent"
        data-testid="chat-message"
        data-role="assistant"
        data-settling="true"
      >
        {stored}
      </ChatTurn>
    );
  }
  return (
    <ChatTurn
      from="agent"
      label="The agent is answering:"
      data-testid="chat-live-reply"
      data-writing={writing ? "true" : undefined}
      aria-busy="true"
      aria-live="off"
      contentTestId="chat-live-text"
      footer={
        <span className="shimmer">
          {writing
            ? "Writing"
            : text
              ? replyActivity(steps, false)
              : "Answering"}
        </span>
      }
    >
      {text ?? (
        <span className="text-muted-foreground">
          {replyActivity(steps, status === "queued")}
        </span>
      )}
    </ChatTurn>
  );
}
