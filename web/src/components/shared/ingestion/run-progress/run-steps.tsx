import { FileText } from "lucide-react";

import { Trace, TraceIcon, TraceLine, TraceTitle } from "@/components/ui/trace";

import {
  tokens,
  type Run,
  type RunStep,
} from "@/features/ingestion/api/runs-api";
import {
  countOf,
  DELETED_DOCUMENT,
  stepKind,
  stepStatusWords,
} from "@/features/ingestion/helpers/run-words";

/** How a step's line looks: at work, waiting its turn, or failed. */
function lineStatus(step: RunStep) {
  if (step.status === "running") return "running";
  if (step.status === "planned") return "pending";
  if (step.status === "failed") return "error";
  return undefined;
}

/** Every step of the run, one line each: its document, then what it does, found and cost. */
export function RunSteps({ run }: { run: Run }) {
  return (
    <Trace role="list" aria-label="Steps of this run" className="gap-3">
      {run.steps.map((step) => (
        <div
          key={step.id}
          role="listitem"
          className="flex min-w-0 flex-col gap-1"
        >
          <TraceLine status={lineStatus(step)}>
            <TraceIcon>
              <FileText />
            </TraceIcon>
            <TraceTitle>
              <bdi>{step.document_name ?? DELETED_DOCUMENT}</bdi>
            </TraceTitle>
          </TraceLine>
          <p className="pl-6 text-sm break-words text-muted-foreground">
            {stepKind(step, run.steps)}. {stepStatusWords(step)}
            {step.status === "done" || step.status === "running"
              ? `, ${countOf(step.items, "item", "items")}`
              : null}
            {step.refused > 0 ? `, ${tokens(step.refused)} refused` : null}
            {step.tokens !== null
              ? `, ${tokens(step.tokens)} tokens in its last attempt`
              : null}
            {step.cost_usd !== null
              ? `, about ${step.cost_usd} USD for that attempt`
              : null}
            .{step.error ? ` ${step.error}` : null}
          </p>
        </div>
      ))}
    </Trace>
  );
}
