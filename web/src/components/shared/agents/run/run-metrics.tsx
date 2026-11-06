import { useId, type ReactNode } from "react";

import type { Run } from "@/features/agents/api/runs-api";
import {
  costLabel,
  costNote,
  elapsed,
  formatCost,
  formatDuration,
  formatTokens,
} from "@/features/agents/helpers/format";

function Metric({
  label,
  value,
  describedBy,
}: {
  label: string;
  value: ReactNode;
  /** The id of a sentence that says what the value is (the cost note). */
  describedBy?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        aria-describedby={describedBy}
        className="truncate text-sm font-medium tabular-nums"
      >
        {value}
      </dd>
    </div>
  );
}

/** A run's model, tokens, cost and duration, and what the amount is when it is not a cost. */
export function RunMetrics({ run, now }: { run: Run; now: number }) {
  const noteId = useId();
  // What the amount is, when it is not a plain cost: on a subscription, an estimate.
  const note = run.cost_usd !== null ? costNote(run.credential_kind) : null;
  return (
    <>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="Model" value={run.model ?? "Default"} />
        <Metric
          label="Tokens"
          value={formatTokens(run.input_tokens, run.output_tokens)}
        />
        <Metric
          label={
            run.cost_usd === null ? "Cost" : costLabel(run.credential_kind)
          }
          value={formatCost(run.cost_usd)}
          describedBy={note ? noteId : undefined}
        />
        <Metric label="Duration" value={formatDuration(elapsed(run, now))} />
      </dl>
      {/* Tied to the amount for assistive technology (aria-describedby on its dd). */}
      {note ? (
        <p
          id={noteId}
          data-testid="cost-note"
          className="text-sm text-muted-foreground"
        >
          {note}
        </p>
      ) : null}
    </>
  );
}
