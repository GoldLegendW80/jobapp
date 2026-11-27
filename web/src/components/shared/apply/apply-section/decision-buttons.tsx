import { Button } from "@/components/ui/button";

import type { CancelKind } from "@/features/apply/helpers/apply-section-rules";
import type { Phase } from "@/features/apply/helpers/apply-state";
import type { DecisionKind } from "@/features/apply/helpers/decisions";

/**
 * The decisions that ask first: after a click the app could not confirm, "It was sent", "It was
 * not sent" and Try again (unless a question asked after the send stands in for it), "It was not
 * sent" first when the assistant says nothing was sent (`notSentFirst`); after a refusal, Try
 * again; once the assistant stopped waiting for the person, Try again (`askRetry`, A25); Cancel
 * application at any moment before an end (`cancelKind`, A24).
 */
export function DecisionButtons({
  phase,
  busy,
  questionAfterSend,
  retryKind,
  askRetry = null,
  notSentFirst = false,
  cancelKind,
  onAsk,
}: {
  phase: Phase;
  busy: boolean;
  questionAfterSend: boolean;
  retryKind: DecisionKind;
  askRetry?: DecisionKind | null;
  notSentFirst?: boolean;
  cancelKind: CancelKind | null;
  onAsk: (kind: DecisionKind) => void;
}) {
  const sent =
    phase === "unknown" ? (
      <Button
        variant={notSentFirst ? "outline" : "default"}
        className="w-full @sm:w-fit"
        disabled={busy}
        onClick={() => {
          onAsk("submitted");
        }}
      >
        It was sent
      </Button>
    ) : null;
  return (
    <>
      {notSentFirst ? null : sent}
      {cancelKind === "notSent" ? (
        <Button
          variant={notSentFirst ? "default" : "outline"}
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("notSent");
          }}
        >
          It was not sent
        </Button>
      ) : null}
      {notSentFirst ? sent : null}
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
      {phase === "refused" || askRetry ? (
        <Button
          className="w-full @sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk(askRetry ?? "retry");
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
