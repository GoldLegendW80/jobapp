import {
  Ban,
  Brain,
  CircleAlert,
  CircleCheck,
  CircleDot,
  CircleX,
  Gauge,
  MessageSquareText,
  ShieldX,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

import type { CredentialKind, RunStep } from "@/features/agents/api/runs-api";
import type { LiveText } from "@/features/agents/helpers/live-text";
import {
  timelineItems,
  type StepIcon,
} from "@/features/agents/helpers/run-steps";
import { StepBody } from "./step-body";

const ICON: Record<StepIcon, LucideIcon> = {
  failed: CircleX,
  cancelled: Ban,
  succeeded: CircleCheck,
  status: CircleDot,
  measure: Gauge,
  thinking: Brain,
  answer: MessageSquareText,
  tool: Wrench,
  problem: CircleAlert,
  refused: ShieldX,
};

/** The steps of a run, oldest first, the answer being written last. */
export function RunTimeline({
  steps,
  live,
  kind,
}: {
  steps: RunStep[];
  live: LiveText | null;
  kind: CredentialKind | null | undefined;
}) {
  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No step yet.</p>;
  }
  const items = timelineItems(steps, live, kind);
  const latest = items.at(-1)?.view.title ?? "";
  return (
    <>
      {/* Screen readers hear the newest step's title only: "Answer" once when the answer
          starts, not every piece of it, and not again when the stored step replaces it. */}
      <p className="sr-only" role="status" aria-live="polite">
        {latest}
      </p>
      <ol aria-label="Steps" className="flex flex-col">
        {items.map(({ key, kind, view, live: writing }) => {
          const Icon = ICON[view.icon];
          return (
            <li
              key={key}
              data-kind={kind}
              data-live={writing ? "true" : undefined}
              aria-busy={writing ? "true" : undefined}
              className="group relative flex gap-3 pb-4 last:pb-0"
            >
              <span
                aria-hidden="true"
                className="absolute top-6 bottom-0 left-[11px] w-px bg-border group-last:hidden"
              />
              <Icon
                aria-hidden="true"
                className={cn(
                  "mt-0.5 size-6 shrink-0 rounded-full border bg-background p-1",
                  view.tone === "error" &&
                    "border-destructive text-destructive",
                  view.tone === "warning" && "border-warning text-warning",
                  view.tone === "muted" && "text-muted-foreground",
                )}
              />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p
                  className={cn(
                    "text-sm font-medium",
                    view.tone === "error" && "text-destructive",
                    view.tone === "muted" && "text-muted-foreground",
                  )}
                >
                  {view.title}
                </p>
                {view.body ? <StepBody body={view.body} /> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}
