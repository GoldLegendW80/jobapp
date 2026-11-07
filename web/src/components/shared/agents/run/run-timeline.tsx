import { Trace } from "@/components/ui/trace";

import type { CredentialKind, RunStep } from "@/features/agents/api/runs-api";
import type { LiveText } from "@/features/agents/helpers/live-text";
import { timelineItems } from "@/features/agents/helpers/run-steps";
import { TimelineStep } from "./timeline-step";

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
      <Trace role="list" aria-label="Steps" className="gap-3">
        {items.map(({ key, kind, view, live: writing }) => (
          <div
            key={key}
            role="listitem"
            data-kind={kind}
            data-live={writing ? "true" : undefined}
            aria-busy={writing ? "true" : undefined}
            className="flex min-w-0 flex-col gap-1"
          >
            <TimelineStep view={view} writing={writing} />
          </div>
        ))}
      </Trace>
    </>
  );
}
