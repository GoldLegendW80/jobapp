import { Link } from "react-router";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import type { FlowStep } from "../api/activity-api";
import { durationWords, secondsOf, stepHref } from "../helpers/activity";
import { agentWord } from "@/lib/wording";
import { FlowStatusIcon } from "./flow-status-icon";

/**
 * The steps of a workflow run, in the order they started, as the job list on the side of a
 * GitHub Actions run: an agent step opens its log, an app step says what it did.
 */
export function WorkflowRunSteps({
  runId,
  steps,
  selected,
  now,
}: {
  runId: string;
  steps: FlowStep[];
  selected: string | null;
  now: number;
}) {
  if (!steps.length) {
    return (
      <p className="px-1 text-sm text-muted-foreground">No step started yet.</p>
    );
  }
  return (
    <Card className="gap-0 overflow-hidden py-1">
      <ol aria-label="Steps of this run" className="flex flex-col">
        {steps.map((step) => {
          const took = durationWords(secondsOf(step, now));
          const body = (
            <>
              <FlowStatusIcon status={step.status} className="mt-0.5" />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">{step.name}</span>
                {step.note ? (
                  <span className="text-xs text-muted-foreground">
                    {step.note}
                  </span>
                ) : step.agent ? (
                  <span className="sr-only">, {agentWord(step.agent)}</span>
                ) : null}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {took ?? ""}
              </span>
            </>
          );
          const row = "flex min-w-0 items-start gap-2 px-3 py-2 text-sm";
          return (
            <li key={step.id} data-kind={step.kind} data-status={step.status}>
              {step.kind === "agent" ? (
                <Link
                  to={stepHref(runId, step.id)}
                  replace
                  preventScrollReset
                  aria-current={selected === step.id ? "step" : undefined}
                  className={cn(
                    row,
                    "outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset aria-[current=step]:bg-accent aria-[current=step]:font-medium",
                  )}
                >
                  {body}
                </Link>
              ) : (
                <div className={row}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
