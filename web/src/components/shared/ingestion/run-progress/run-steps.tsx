import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";

import { tokens, type Run } from "@/features/ingestion/api/runs-api";
import {
  countOf,
  DELETED_DOCUMENT,
  stepKind,
  stepStatusWords,
} from "@/features/ingestion/helpers/run-words";
import { FoldTrigger } from "./fold-trigger";

/** Every step of the run, folded: its document, what it does, what it found and cost. */
export function RunSteps({ run }: { run: Run }) {
  return (
    <Collapsible className="rounded-lg bg-muted">
      <FoldTrigger>Every step of this run</FoldTrigger>
      <CollapsibleContent>
        <ol className="flex flex-col gap-2 px-3 pb-3">
          {run.steps.map((step) => (
            <li key={step.id} className="min-w-0 break-words">
              <span className="font-medium">
                {step.document_name ?? DELETED_DOCUMENT}
              </span>
              . {stepKind(step, run.steps)}. {stepStatusWords(step)}
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
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
