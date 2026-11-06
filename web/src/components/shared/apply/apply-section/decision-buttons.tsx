import { Button } from "@/components/ui/button";

import type { Phase } from "@/features/apply/helpers/apply-state";
import type { DecisionKind } from "@/features/apply/helpers/decisions";

/**
 * The decisions that ask first: after a click the app could not confirm, "It was submitted" and
 * Try again (unless a question asked after the send stands in for it); after a refusal, Try again;
 * Cancel application wherever the api still takes it.
 */
export function DecisionButtons({
  phase,
  busy,
  questionAfterSend,
  retryKind,
  cancellable,
  onAsk,
}: {
  phase: Phase;
  busy: boolean;
  questionAfterSend: boolean;
  retryKind: DecisionKind;
  cancellable: boolean;
  onAsk: (kind: DecisionKind) => void;
}) {
  return (
    <>
      {phase === "unknown" ? (
        <>
          <Button
            className="w-full sm:w-fit"
            disabled={busy}
            onClick={() => {
              onAsk("submitted");
            }}
          >
            It was submitted
          </Button>
          {questionAfterSend ? null : (
            <Button
              variant="outline"
              className="w-full sm:w-fit"
              disabled={busy}
              onClick={() => {
                onAsk(retryKind);
              }}
            >
              Try again
            </Button>
          )}
        </>
      ) : null}
      {phase === "refused" ? (
        <Button
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("retry");
          }}
        >
          Try again
        </Button>
      ) : null}
      {cancellable ? (
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("cancel");
          }}
        >
          Cancel application
        </Button>
      ) : null}
    </>
  );
}
