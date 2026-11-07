/**
 * RunPanel: one agent run, live. A timeline of its steps as the worker writes them (text, tool
 * calls and their results, refused tools, usage, status changes), the model, tokens, cost and
 * duration, a Cancel button while it is queued or running, and the state of the live connection
 * (it reconnects by itself after a network loss and picks up exactly where it stopped).
 * The answer appears while it is written (issue #33): the last item of the timeline grows with
 * the text, then the stored step takes its place, same title, same markup, in one render.
 * Embedded where a run is a part of a page (the posting check, a reading); /runs/:id lays the
 * same parts out as a page of its own.
 */
import { CircleAlert } from "lucide-react";
import { useRef } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { agentWord } from "@/lib/wording";

import { useNow } from "@/features/agents/hooks/use-now";
import { useReturnFocus } from "@/features/agents/hooks/use-return-focus";
import { useRunStream } from "@/features/agents/api/use-run-stream";
import { CancelRunButton } from "./run/cancel-run-button";
import { ConnectionState } from "./run/connection-state";
import { RunMetrics } from "./run/run-metrics";
import { RunStatusBadge } from "./run/run-status-badge";
import { RunTimeline } from "./run/run-timeline";

export function RunPanel({
  runId,
  title,
  cancellable = true,
}: {
  runId: string;
  title?: string;
  /** False where the page has a Cancel of its own that stops more than this agent run. */
  cancellable?: boolean;
}) {
  const { detail, steps, live, status, connection, gone } = useRunStream(runId);
  const run = detail.data;
  const active = status === "queued" || status === "running";
  const now = useNow(active);
  const titleRef = useRef<HTMLSpanElement>(null);
  // The Cancel button goes away when the run ends: its focus goes to the title, which now
  // says the run's status.
  const cancelFocus = useReturnFocus(active, titleRef);

  if (detail.isPending) {
    return <LoadingCard>Loading the run</LoadingCard>;
  }
  // Only when there is nothing to show: a failed refresh keeps the panel and its timeline.
  if (gone) {
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertDescription>This run no longer exists.</AlertDescription>
      </Alert>
    );
  }
  if (!run) {
    return <LoadError query={detail}>The run could not be loaded.</LoadError>;
  }
  const shownStatus = status ?? run.status;
  const stopping = run.cancel_requested && active;
  return (
    <Card data-testid="run-panel" data-status={shownStatus}>
      <CardHeader>
        <CardTitle>
          <span
            ref={titleRef}
            tabIndex={-1}
            data-testid="run-title"
            className="flex flex-wrap items-center gap-2 rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <span>{title ?? agentWord(run.agent)}</span>
            <RunStatusBadge status={shownStatus} />
          </span>
        </CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-3">
          <ConnectionState state={connection} />
          {detail.isRefetchError ? (
            <span className="text-xs text-warning">
              Could not refresh the totals; they update with the next step.
            </span>
          ) : null}
        </CardDescription>
        {active && cancellable ? (
          <CardAction>
            <CancelRunButton
              runId={runId}
              stopping={stopping}
              size="sm"
              {...cancelFocus}
            />
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <RunMetrics run={run} now={now} />
        <RunTimeline steps={steps} live={live} kind={run.credential_kind} />
      </CardContent>
    </Card>
  );
}
