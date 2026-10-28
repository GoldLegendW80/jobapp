/**
 * "Find jobs" on /jobs: start a search of the job websites ticked in its setup (every search
 * runs the websites alone, owner 2026-10-28), follow it live (the website being read, new
 * postings so far), then a summary of what it found. An older sweep that read boards still
 * shows them. The history of the last sweeps, with the automatic schedule and its next run, is one
 * click away. A sweep already running when the page opens (started elsewhere, or an automatic
 * one) is followed the same way.
 */
import { useQuery } from "@tanstack/react-query";
import {
  CalendarClock,
  CircleAlert,
  History,
  LoaderCircle,
  Search,
  TriangleAlert,
} from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isFeedReading } from "@/features/opportunities/sources/source-words";
import { describeError } from "@/lib/api";

import { FitBadge, StatusBadge } from "./badges";
import { formatDateTime } from "./format";
import { plural } from "./selection";
import { ScheduleDialog } from "./schedule-dialog";
import { sourceLabel, sourceName } from "./labels";
import { SweepSources } from "./sweep-sources";
import { SearchSetup } from "./search-setup";
import { getSweep, listSweeps, sweepsKey, type Sweep } from "./sweeps-api";
import { scheduleLine } from "./use-schedule";
import type { SweepRunner } from "./use-sweep-runner";

export function FindJobsButton({ runner }: { runner: SweepRunner }) {
  const [setup, setSetup] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const busy = runner.starting || runner.running !== null;
  return (
    <>
      <Button
        ref={trigger}
        onClick={() => {
          setSetup(true);
        }}
        disabled={busy}
      >
        {busy ? (
          <LoaderCircle aria-hidden="true" className="animate-spin" />
        ) : (
          <Search aria-hidden="true" />
        )}
        {busy ? "Finding jobs" : "Find jobs"}
      </Button>
      {setup ? (
        <SearchSetup
          runner={runner}
          onClose={() => {
            setSetup(false);
          }}
          onReturnFocus={() => trigger.current?.focus()}
        />
      ) : null}
    </>
  );
}

/** What the sweep reads now: a website searched in the person's browser by its name (its
 * `current_board` is the source's id, jobs J10), any other source by its label. */
function readingName(sweep: Sweep): string {
  const current = sweep.current_board ?? "";
  const source = sweep.sources.find((item) => item.source_id === current);
  return source ? sourceName(source) : sourceLabel(current);
}

/** The live line under the header while a sweep runs. */
export function SweepProgress({ runner }: { runner: SweepRunner }) {
  const sweep = runner.running;
  if (!sweep) return null;
  const total = sweep.boards_total;
  const percent = total ? Math.round((100 * sweep.boards_done) / total) : 0;
  return (
    <section
      aria-label="Sweep in progress"
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 text-card-foreground"
    >
      <div
        role="status"
        aria-live="polite"
        className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
      >
        <span className="font-medium">
          {total
            ? `${sweep.boards_done} of ${plural(total, "board")} read`
            : sweep.current_board
              ? isFeedReading(sweep.current_board)
                ? "Reading freelance and project sources"
                : "Reading job sites in your browser"
              : "Waiting for the worker"}
        </span>
        {sweep.current_board ? (
          <span className="min-w-0 truncate text-muted-foreground">
            Reading {readingName(sweep)}
          </span>
        ) : null}
        <span className="tabular-nums text-muted-foreground">
          {plural(sweep.new, "new posting")}
          {sweep.boards_failed.length
            ? `, ${plural(sweep.boards_failed.length, "board")} failed`
            : ""}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Boards read"
        aria-valuemin={0}
        aria-valuemax={total || 1}
        aria-valuenow={sweep.boards_done}
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500 motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
    </section>
  );
}

function summaryLine(sweep: Sweep): string {
  const parts = [
    // A sweep that read no board (every sweep since the owner's decision of 2026-10-28, which
    // searches the ticked websites alone) counts the websites it searched instead.
    sweep.boards_total > 0
      ? `${plural(sweep.boards_ok, "board")} read`
      : `${plural(sweep.sources.filter((source) => source.host).length, "website")} searched`,
    `${plural(sweep.seen, "posting")} seen`,
    `${sweep.matched.toLocaleString("en-GB")} matched your search`,
    plural(sweep.new, "new posting"),
  ];
  return `${parts.join(", ")}.`;
}

export function SweepSummaryDialog({ runner }: { runner: SweepRunner }) {
  const sweep = runner.summary;
  if (!sweep) return null;
  const failed = sweep.status === "failed";
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) runner.showSummary(null);
      }}
    >
      {/* Grid children may shrink below their content, so long lines truncate or wrap. */}
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>
            {failed
              ? "The sweep stopped"
              : sweep.new
                ? `${plural(sweep.new, "new posting")} found`
                : "No new posting"}
          </DialogTitle>
          <DialogDescription>
            {summaryLine(sweep)}
            {sweep.duplicates
              ? ` ${plural(sweep.duplicates, "match", "matches")} already in your list.`
              : ""}
          </DialogDescription>
        </DialogHeader>
        {sweep.error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{sweep.error}</AlertDescription>
          </Alert>
        ) : null}
        {sweep.skipped_by_rule ? (
          <p className="text-sm text-muted-foreground">
            {plural(sweep.skipped_by_rule, "new posting")} matched a never apply
            rule and {sweep.skipped_by_rule === 1 ? "was" : "were"} set to
            Skipped.
          </p>
        ) : null}
        {sweep.new_jobs.length ? (
          <section aria-label="New postings" className="flex flex-col gap-1">
            <h3 className="text-sm font-medium">New postings</h3>
            <ul className="flex flex-col divide-y rounded-md border">
              {sweep.new_jobs.map((job) => (
                <li key={job.id}>
                  <Link
                    to={`/jobs/${job.id}`}
                    onClick={() => {
                      runner.showSummary(null);
                    }}
                    className="flex items-center gap-3 px-3 py-2 hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium">
                        {job.title}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">
                        {job.company}
                        {job.location ? `, ${job.location}` : ""}
                      </span>
                    </span>
                    {job.status === "skipped" ? (
                      <StatusBadge status={job.status} />
                    ) : null}
                    <FitBadge fit={job.fit} />
                  </Link>
                </li>
              ))}
            </ul>
            {sweep.new > sweep.new_jobs.length ? (
              <p className="text-xs text-muted-foreground">
                The first {sweep.new_jobs.length} by fit. Every one is in the
                list with the status Found.
              </p>
            ) : null}
          </section>
        ) : null}
        <SweepSources sources={sweep.sources} />
        {sweep.boards_failed.length ? (
          <section aria-label="Failed boards" className="flex flex-col gap-1">
            <h3 className="flex items-center gap-1.5 text-sm font-medium">
              <TriangleAlert
                aria-hidden="true"
                className="size-4 text-amber-600 dark:text-amber-400"
              />
              {plural(sweep.boards_failed.length, "board")} could not be read
            </h3>
            <ul className="flex flex-col gap-0.5 text-sm break-words">
              {sweep.boards_failed.map((board) => (
                <li key={`${board.ats}:${board.org}`}>
                  <span className="font-medium">{board.company}</span>{" "}
                  <span className="text-muted-foreground">
                    ({board.ats}, {board.org}): {board.message}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <DialogFooter>
          <Button
            onClick={() => {
              runner.showSummary(null);
            }}
          >
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const STATUS_TEXT: Record<string, string> = {
  running: "Running",
  done: "Done",
  failed: "Failed",
};

/** The last sweeps and the automatic schedule, in a dialog. */
export function SweepHistoryButton({ runner }: { runner: SweepRunner }) {
  const [open, setOpen] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const history = useQuery({
    queryKey: sweepsKey,
    queryFn: ({ signal }) => listSweeps(signal),
    enabled: open,
  });
  const schedule = history.data?.schedule;

  const show = (id: string) => {
    setOpening(id);
    getSweep(id).then(
      (detail) => {
        setOpening(null);
        setOpen(false);
        runner.showSummary(detail);
      },
      (error: unknown) => {
        setOpening(null);
        toast.error(`This sweep could not be opened. ${describeError(error)}`);
      },
    );
  };

  return (
    <>
      <Button
        variant="outline"
        size="icon"
        aria-label="Sweep history"
        title="Sweep history"
        onClick={() => {
          setOpen(true);
        }}
      >
        <History aria-hidden="true" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg [&>*]:min-w-0">
          <DialogHeader>
            <DialogTitle>Sweeps</DialogTitle>
            <DialogDescription>
              The last searches for postings.
            </DialogDescription>
          </DialogHeader>
          <section
            aria-label="Automatic search"
            className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm"
          >
            <CalendarClock aria-hidden="true" className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{scheduleLine(schedule)}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={!schedule}
              onClick={() => {
                setEditing(true);
              }}
            >
              Change schedule
            </Button>
          </section>
          {history.isPending ? (
            <p className="text-sm text-muted-foreground">Loading</p>
          ) : history.isError ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                The sweeps could not be loaded. {describeError(history.error)}
              </AlertDescription>
            </Alert>
          ) : history.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No sweep yet. Find jobs searches the websites ticked in its setup.
            </p>
          ) : (
            <ul
              aria-label="Last sweeps"
              className="flex flex-col divide-y rounded-md border"
            >
              {history.data.items.map((sweep) => (
                <li key={sweep.id}>
                  <button
                    type="button"
                    disabled={sweep.status === "running" || opening !== null}
                    onClick={() => {
                      show(sweep.id);
                    }}
                    className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none disabled:cursor-default disabled:hover:bg-transparent"
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="font-medium">
                        {formatDateTime(sweep.started_at)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {sweep.trigger === "schedule" ? "Automatic" : "By hand"}
                        , {STATUS_TEXT[sweep.status] ?? sweep.status}
                        {sweep.boards_failed.length
                          ? `, ${plural(sweep.boards_failed.length, "board")} failed`
                          : ""}
                      </span>
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      {opening === sweep.id ? (
                        <LoaderCircle
                          aria-label="Opening"
                          className="size-4 animate-spin"
                        />
                      ) : (
                        `${sweep.new} new`
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      </Dialog>
      {editing ? (
        <ScheduleDialog
          state={schedule}
          onClose={() => {
            setEditing(false);
          }}
        />
      ) : null}
    </>
  );
}
