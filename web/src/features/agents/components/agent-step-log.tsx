import { ChevronRight, Search } from "lucide-react";
import { useId, useState, type RefObject } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Trace } from "@/components/ui/trace";
import { CancelRunButton } from "@/components/shared/agents/run/cancel-run-button";
import { ConnectionState } from "@/components/shared/agents/run/connection-state";
import { RunMetrics } from "@/components/shared/agents/run/run-metrics";
import { RunStatusBadge } from "@/components/shared/agents/run/run-status-badge";
import { TimelineStep } from "@/components/shared/agents/run/timeline-step";
import { cn } from "@/lib/utils";

import { useRunStream } from "../api/use-run-stream";
import {
  filterGroups,
  logGroups,
  offsetWords,
  type LogGroup,
} from "../helpers/activity";
import { elapsed, formatDuration } from "../helpers/format";
import { timelineItems } from "../helpers/run-steps";
import { useNow } from "../hooks/use-now";
import { useReturnFocus } from "../hooks/use-return-focus";
import { RunLiveView } from "./run-live-view";

function Group({ group, start }: { group: LogGroup; start: string | null }) {
  const [open, setOpen] = useState(group.open);
  const time = offsetWords(group.at, start);
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      role="listitem"
      data-kind={group.items[0]?.kind}
      data-live={group.live ? "true" : undefined}
      aria-busy={group.live ? "true" : undefined}
    >
      <CollapsibleTrigger className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50">
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            group.head.tone === "error" && "text-destructive",
            group.live && "shimmer",
          )}
        >
          {group.head.title}
        </span>
        {time ? (
          <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
            <span className="sr-only">at </span>
            {time}
          </span>
        ) : null}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-1 pb-2 pl-8">
        <Trace role="list" className="gap-2">
          {group.items.map((item) => (
            <div
              key={item.key}
              role="listitem"
              data-kind={item.kind}
              className="flex min-w-0 flex-col gap-1"
            >
              <TimelineStep view={item.view} writing={item.live} />
            </div>
          ))}
        </Trace>
      </CollapsibleContent>
    </Collapsible>
  );
}

/**
 * The log of one agent step, live while it runs, as the log of a GitHub Actions step: its groups
 * fold and unfold, a filter keeps the lines that hold some words, each line has its time from
 * the step's start. A private run's log shows names and sizes only (run-steps.ts). A website
 * search also shows the browser it reads.
 */
export function AgentStepLog({
  runId,
  title,
  titleRef,
}: {
  runId: string;
  /** The step's name, as the steps and the graph say it. */
  title: string;
  /** Where the focus goes when Cancel goes away with it, at the run's end. */
  titleRef: RefObject<HTMLHeadingElement | null>;
}) {
  const filterId = useId();
  const [filter, setFilter] = useState("");
  const { detail, steps, live, status, connection, gone } = useRunStream(runId);
  const run = detail.data;
  const shown = status ?? run?.status;
  const active = shown === "queued" || shown === "running";
  const now = useNow(active);
  const cancelFocus = useReturnFocus(active, titleRef);
  if (gone) {
    return (
      <p className="text-sm text-muted-foreground">
        This step&apos;s log no longer exists.
      </p>
    );
  }
  if (!run || !shown) {
    return detail.isError ? (
      <LoadError query={detail}>
        This step&apos;s log could not be loaded.
      </LoadError>
    ) : (
      <Card role="status" aria-label="Loading the step's log">
        <CardContent className="flex flex-col gap-3">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </CardContent>
      </Card>
    );
  }
  const groups = logGroups(
    steps,
    timelineItems(steps, live, run.credential_kind),
  );
  const kept = filterGroups(groups, filter);
  const seconds = elapsed(run, now);
  return (
    // One element holds the step's log: the journeys follow a run in it.
    <section
      data-testid="run-panel"
      data-status={shown}
      aria-labelledby={`${filterId}-title`}
      className="flex min-w-0 flex-col gap-3"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id={`${filterId}-title`} className="text-base font-semibold">
          {title}
        </h2>
        <RunStatusBadge status={shown} />
        <span className="text-xs text-muted-foreground tabular-nums">
          {seconds === null ? "" : formatDuration(seconds)}
        </span>
        <span className="ml-auto">
          {active ? (
            <CancelRunButton
              runId={runId}
              stopping={run.cancel_requested}
              {...cancelFocus}
            />
          ) : null}
        </span>
      </div>
      <RunMetrics run={run} now={now} className="@lg:grid-cols-4" />
      <RunLiveView run={run} />
      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <InputGroup className="h-8 min-w-0 flex-1">
            <InputGroupAddon>
              <Search aria-hidden="true" />
            </InputGroupAddon>
            <InputGroupInput
              type="search"
              aria-label="Filter the log"
              value={filter}
              placeholder="Filter the log"
              onChange={(event) => {
                setFilter(event.target.value);
              }}
            />
          </InputGroup>
          <ConnectionState state={connection} />
          {detail.isRefetchError ? (
            <span className="text-xs text-warning">
              Could not refresh the totals; they update with the next step.
            </span>
          ) : null}
        </div>
        <div
          role="list"
          aria-label="Steps"
          className="flex flex-col gap-0.5 p-2"
        >
          {groups.length === 0 ? (
            <p
              role="listitem"
              className="px-2 py-1.5 text-sm text-muted-foreground"
            >
              No step yet.
            </p>
          ) : kept.length === 0 ? (
            <p
              role="listitem"
              className="px-2 py-1.5 text-sm text-muted-foreground"
            >
              No line of the log holds these words.
            </p>
          ) : (
            kept.map((group) => (
              <Group
                // A filter opens what it keeps: the key changes with it.
                key={filter.trim() ? `${group.key} ${filter}` : group.key}
                group={filter.trim() ? { ...group, open: true } : group}
                start={run.started_at ?? run.created_at}
              />
            ))
          )}
        </div>
        <p className="sr-only" role="status" aria-live="polite">
          {groups.at(-1)?.head.title ?? ""}
        </p>
      </Card>
    </section>
  );
}
