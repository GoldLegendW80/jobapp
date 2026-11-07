/** The words of the chat's polite live region: each new answer (or failure) once, never the
 * answers already there when the conversation opens. */
import { useEffect, useRef, useState } from "react";

import type { ThreadDetail } from "../api/chat-api";
import { newestAnswer, unanswered } from "../helpers/chat-messages";

export function useAnswerAnnouncement(detail: ThreadDetail | undefined) {
  const [announcement, setAnnouncement] = useState("");
  const announced = useRef<number | null | undefined>(undefined);

  useEffect(() => {
    if (!detail) return;
    const answer = newestAnswer(detail);
    const lastFailed = detail.messages
      .filter((m) => unanswered(m, detail.messages))
      .at(-1);
    const key = Math.max(answer?.id ?? 0, lastFailed?.id ?? 0) || null;
    if (announced.current === undefined) {
      announced.current = key;
      return;
    }
    if (key !== null && key !== announced.current) {
      announced.current = key;
      setAnnouncement(
        lastFailed && lastFailed.id === key
          ? `No answer: ${lastFailed.error ?? "the agent failed."}`
          : `Answer: ${answer?.text ?? ""}`,
      );
    }
  }, [detail]);

  return {
    announcement,
    /** The conversation was deleted: the next one starts with nothing announced. */
    forget: () => {
      announced.current = undefined;
    },
  };
}
