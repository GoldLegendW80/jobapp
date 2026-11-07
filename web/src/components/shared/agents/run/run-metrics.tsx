import { useId, type ReactNode } from "react";

import { Fact, Facts } from "@/components/blocks/facts";
import { cn } from "@/lib/utils";

import type { Run } from "@/features/agents/api/runs-api";
import {
  costLabel,
  costNote,
  elapsed,
  formatCost,
  formatDuration,
  formatTokens,
} from "@/features/agents/helpers/format";

/** One fact of a run: its name over its value. */
export function Metric({
  label,
  value,
  describedBy,
  className,
}: {
  label: string;
  value: ReactNode;
  /** The id of a sentence that says what the value is (the cost note). */
  describedBy?: string;
  className?: string;
}) {
  return (
    <Fact label={label} describedBy={describedBy} className={className}>
      {value}
    </Fact>
  );
}

/**
 * A run's model, tokens, cost and duration, and what the amount is when it is not a cost.
 * `before` and `after` add facts of the caller's (Metric elements) around them.
 */
export function RunMetrics({
  run,
  now,
  before,
  after,
  className,
}: {
  run: Run;
  now: number;
  before?: ReactNode;
  after?: ReactNode;
  /** The grid of the facts; four columns from a 32rem wide box when left out. */
  className?: string;
}) {
  const noteId = useId();
  // What the amount is, when it is not a plain cost: on a subscription, an estimate.
  const note = run.cost_usd !== null ? costNote(run.credential_kind) : null;
  return (
    <>
      {/* Four columns when its own box is wide enough, not the window: it sits in narrow rails. */}
      <div className="@container">
        <Facts
          data-testid="run-facts"
          className={cn("@lg:grid-cols-4", className)}
        >
          {before}
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
          {after}
        </Facts>
      </div>
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
