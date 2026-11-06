/**
 * Enter was pressed while nothing could be sent yet (the conversation is loading, or the previous
 * message is still being answered): the text of the draft at that moment, sent as soon as it can
 * go. Nothing leaves without an Enter on that exact text: any edit, a cleared draft, a send or
 * another job drops the hold.
 */
import { useState } from "react";

export function useHeldMessage(jobId: string) {
  const [heldFor, setHeldFor] = useState<{ job: string; text: string } | null>(
    null,
  );
  // Another job is another conversation: nothing held for the previous one goes out.
  const held = heldFor?.job === jobId ? heldFor.text : null;
  if (heldFor !== null && heldFor.job !== jobId) {
    setHeldFor(null);
  }
  return {
    held,
    hold: (text: string) => {
      setHeldFor({ job: jobId, text });
    },
    drop: () => {
      setHeldFor(null);
    },
  };
}
