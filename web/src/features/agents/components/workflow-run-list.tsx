import { Clock, Zap } from "lucide-react";
import { Link } from "react-router";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";

import type { WorkflowRun } from "../api/activity-api";
import { durationWords, secondsOf } from "../helpers/activity";
import { FlowStatusIcon } from "./flow-status-icon";

/**
 * The runs of the selected workflow, newest first, as the run list of GitHub Actions: each with
 * its status, its subject (or the workflow's name), the workflow and how it was triggered, when
 * it started and how long it took.
 */
export function WorkflowRunList({
  runs,
  now,
  showWorkflow,
}: {
  runs: WorkflowRun[];
  now: number;
  /** All workflows: each row names its workflow. */
  showWorkflow: boolean;
}) {
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <ul aria-label="Workflow runs" className="divide-y">
        {runs.map((run) => {
          const took = durationWords(secondsOf(run, now));
          return (
            <li key={run.id} data-status={run.status}>
              <Link
                to={`/activity/runs/${encodeURIComponent(run.id)}`}
                className="flex min-w-0 items-start gap-3 px-4 py-3 outline-none hover:bg-accent/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset"
              >
                <FlowStatusIcon status={run.status} className="mt-0.5" />
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="truncate text-sm font-medium">
                    {run.subject?.words ?? run.workflow_title}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {showWorkflow && run.subject ? (
                      <>
                        <span className="font-medium text-foreground/80">
                          {run.workflow_title}
                        </span>
                        {". "}
                      </>
                    ) : null}
                    {run.trigger.words}
                  </span>
                </span>
                <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted-foreground tabular-nums">
                  <span className="flex items-center gap-1">
                    <Zap aria-hidden="true" className="size-3" />
                    <span className="sr-only">Started </span>
                    {formatDateTime(run.created_at)}
                  </span>
                  {took ? (
                    <span className="flex items-center gap-1">
                      <Clock aria-hidden="true" className="size-3" />
                      <span className="sr-only">Took </span>
                      {took}
                    </span>
                  ) : null}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export function WorkflowRunListLoading() {
  return (
    <Card
      className="gap-0 py-0"
      role="status"
      aria-label="Loading the workflow runs"
    >
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className="flex items-center gap-3 border-b px-4 py-4">
          <Skeleton className="size-4 rounded-full" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-24" />
        </div>
      ))}
    </Card>
  );
}
