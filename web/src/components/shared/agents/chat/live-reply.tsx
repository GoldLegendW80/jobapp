import { useEffect, useLayoutEffect, useRef } from "react";

import { Spinner } from "@/components/ui/spinner";

import { FINAL_STATUSES } from "@/features/agents/api/runs-api";
import { useRunStream } from "@/features/agents/api/use-run-stream";
import { replyActivity } from "@/features/agents/helpers/chat-messages";
import { lastText } from "@/features/agents/helpers/live-text";
import { ChatBubble } from "./chat-bubble";

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
  onGrow,
}: {
  runId: string;
  onEnd: () => void;
  onGrow: () => void;
}) {
  const { steps, live, status } = useRunStream(runId);
  const ended = status !== undefined && FINAL_STATUSES.has(status);
  const endRef = useRef(onEnd);
  const growRef = useRef(onGrow);
  useEffect(() => {
    endRef.current = onEnd;
    growRef.current = onGrow;
  });
  useEffect(() => {
    if (ended) endRef.current();
  }, [ended]);
  const stored = lastText(steps);
  const writing = live?.text ? live.text : undefined;
  const text = writing ?? stored;
  // Before paint: the list is at its bottom again in the frame that shows the new text.
  useLayoutEffect(() => {
    growRef.current();
  }, [text, ended]);
  if (ended) {
    if (status !== "done" || stored === undefined) return null;
    return (
      <li
        data-testid="chat-message"
        data-role="assistant"
        data-settling="true"
        className="flex flex-col items-start gap-1"
      >
        <span className="sr-only">Agent:</span>
        <ChatBubble from="agent">{stored}</ChatBubble>
      </li>
    );
  }
  return (
    <li
      data-testid="chat-live-reply"
      data-writing={writing ? "true" : undefined}
      className="relative flex flex-col items-start gap-1"
      aria-busy="true"
      aria-live="off"
    >
      <span className="sr-only">The agent is answering:</span>
      <ChatBubble from="agent" data-testid="chat-live-text">
        {text ?? (
          <span className="text-muted-foreground">
            {replyActivity(steps, status === "queued")}
          </span>
        )}
      </ChatBubble>
      {/* Out of the flow, in the space the list keeps free below its last item: the bubble
          does not move when this line goes. */}
      <span className="absolute top-full mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <Spinner
          aria-hidden="true"
          role={undefined}
          className="size-3 motion-reduce:animate-none"
        />
        {writing ? "Writing" : text ? replyActivity(steps, false) : "Answering"}
      </span>
    </li>
  );
}
