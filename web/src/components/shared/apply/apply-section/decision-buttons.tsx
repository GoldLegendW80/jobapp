import { Button } from "@/components/ui/button";

import type { CancelKind } from "@/features/apply/helpers/apply-section-rules";
import type { Phase } from "@/features/apply/helpers/apply-state";
import type { DecisionKind } from "@/features/apply/helpers/decisions";

/**
 * The decisions that ask first: after a click the app could not confirm, "It was submitted", "It
 * was not sent" and Try again (unless a question asked after the send stands in for it); after a
 * refusal, Try again; Cancel application at any moment before an end (`cancelKind`, A24).
 */
export function DecisionButtons({
  phase,
  busy,
  questionAfterSend,
  retryKind,
  cancelKind,
  onAsk,
}: {
  phase: Phase;
  busy: boolean;
  questionAfterSend: boolean;
  retryKind: DecisionKind;
  cancelKind: CancelKind | null;
  onAsk: (kind: DecisionKind) => void;
}) {
  return (
    <>
      {phase === "unknown" ? (
        <Button
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("submitted");
          }}
        >
          It was submitted
        </Button>
      ) : null}
      {cancelKind === "notSent" ? (
        <Button
          variant="outline"
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("notSent");
          }}
        >
          It was not sent
        </Button>
      ) : null}
      {phase === "unknown" && !questionAfterSend ? (
        <Button
          variant="outline"
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk(retryKind);
          }}
        >
          Try again
        </Button>
      ) : null}
      {phase === "refused" ? (
        <Button
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("retry");
          }}
        >
          Try again
        </Button>
      ) : null}
      {cancelKind === "cancel" || cancelKind === "cancelAfterSend" ? (
        <Button
          variant="outline"
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk(cancelKind);
          }}
        >
          Cancel application
        </Button>
      ) : null}
    </>
  );
}
