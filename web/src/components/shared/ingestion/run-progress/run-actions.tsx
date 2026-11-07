import { Button } from "@/components/ui/button";

import {
  PAUSED_ON_CEILING,
  tokens,
  type Run,
} from "@/features/ingestion/api/runs-api";
import type { RunAction } from "@/features/ingestion/api/use-run-actions";

/** The one action that leaves each state of a run, and Cancel while it can still stop. */
export function RunActions({
  run,
  busy,
  onAction,
}: {
  run: Run;
  busy: boolean;
  onAction: (what: RunAction) => void;
}) {
  const adds = run.tokens.continue_adds;
  const cancel = (
    <Button
      variant="outline"
      disabled={busy || run.cancel_requested}
      onClick={() => {
        onAction({ name: "cancel" });
      }}
    >
      Cancel the run
    </Button>
  );
  if (run.status === "running")
    return <div className="flex flex-wrap gap-2">{cancel}</div>;
  if (run.status !== "paused") return null;
  if (run.paused_reason === PAUSED_ON_CEILING)
    return adds !== null && adds > 0 ? (
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={busy}
          onClick={() => {
            // The figure on screen is the figure agreed to, and the one sent.
            onAction({ name: "continue", adds });
          }}
        >
          Continue with {tokens(adds)} more tokens
        </Button>
        {cancel}
      </div>
    ) : null;
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        disabled={busy}
        onClick={() => {
          onAction({ name: "resume" });
        }}
      >
        Resume the run
      </Button>
      {cancel}
    </div>
  );
}
