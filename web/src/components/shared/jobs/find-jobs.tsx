/**
 * "Find jobs" on /jobs: start a search of the company job boards and the job websites ticked in
 * its setup (owner, 2026-10-30), follow it live (the website being read, new
 * postings so far), then a summary of what it found. An older sweep that read boards still
 * shows them. The history of the last sweeps, with the automatic schedule and its next run, is in
 * the page's More actions menu ("Past searches"). A sweep already running when the page opens (started elsewhere,
 * or an automatic one) is followed the same way. The code says "sweep"; the person reads
 * "search" (Chapter 2 of the audit: the word left the interface).
 */
import {
  CalendarClock,
  CircleAlert,
  Search,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";
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
import { Spinner } from "@/components/ui/spinner";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Progress } from "@/components/ui/progress";
import { describeError } from "@/lib/api";

import { FitBadge, StatusBadge } from "@/components/shared/jobs/badges";
import { formatDateTime } from "@/features/jobs/helpers/format";
import { isJobFeed } from "@/features/jobs/helpers/job-feeds";
import {
  countedCards,
  listedReadSentence,
} from "@/features/jobs/helpers/listed-words";
import { keptParts } from "@/features/jobs/helpers/reported-words";
import { plural } from "@/features/jobs/helpers/selection";
import { ScheduleDialog } from "./schedule-dialog";
import { sourceLabel, sourceName } from "@/features/jobs/helpers/labels";
import { SweepBrowsers } from "./sweep-browsers";
import { SweepAssistantWords } from "./sweep-assistant-words";
import { SweepSources } from "./sweep-sources";
import { SearchSetup } from "./search-setup/search-setup";
import { type Sweep } from "@/features/jobs/api/sweeps-api";
import type { FindJobs } from "@/features/jobs/hooks/use-find-jobs";
import { scheduleLine } from "@/features/jobs/api/use-schedule";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { useFetchSweep, useSweeps } from "@/features/jobs/api/use-sweeps";

/** A button that opens the page's Find jobs setup (use-find-jobs.ts); it says when a search
 * runs, and stays usable then, so another search can wait in the line (jobs J33). */
export function FindJobsButton({ finder }: { finder: FindJobs }) {
  const busy = finder.runner.starting || finder.runner.running !== null;
  return (
    <Button
      data-find-jobs=""
      onClick={(event) => {
        finder.show(event.currentTarget);
      }}
      disabled={finder.runner.starting}
    >
      {busy ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : (
        <Search aria-hidden="true" />
      )}
      {busy ? "Finding jobs" : "Find jobs"}
    </Button>
  );
}

/** The setup itself, while open. */
export function FindJobsSetup({ finder }: { finder: FindJobs }) {
  if (!finder.open) return null;
  return (
    <SearchSetup
      runner={finder.runner}
      initial={finder.screen}
      onClose={finder.close}
      onReturnFocus={finder.returnFocus}
    />
  );
}

/** A ticked job feed (mode "api", api app/jobs/feeds). */
function isFeed(source: Sweep["sources"][number]): boolean {
  return source.mode === "api" && isJobFeed(source.kind);
}

/** A company job board (mode "api", kind "board", api app/jobs/boards). */
function isBoard(source: Sweep["sources"][number]): boolean {
  return source.kind === "board";
}

/** A job website of the sweep: a source with a host, but neither a provider's alert e-mails
 * (mode "mail", host "mail:indeed"), nor a job feed, nor a company board. */
function isWebsite(source: Sweep["sources"][number]): boolean {
  return (
    Boolean(source.host) &&
    source.mode !== "mail" &&
    !isFeed(source) &&
    !isBoard(source)
  );
}

/** What the sweep reads now: a website searched in the person's browser by its name (its
 * `current_board` is the source's id, jobs J10), any other source by its label. */
function readingName(sweep: Sweep): string {
  const current = sweep.current_board ?? "";
  const source = sweep.sources.find((item) => item.source_id === current);
  return source ? sourceName(source) : sourceLabel(current);
}

/** The "Reading" part of the live line, null when nothing is read. The sources that make no
 * browser call run side by side (owner, 2026-10-31) and each sets `current_board` when it
 * starts, so it may name one that already ended: several running at once are counted,
 * "Reading 3 sources"; one running is named; `current_board` is read only when no entry is
 * running (an older row whose sources have no phase). */
function readingLine(sweep: Sweep): string | null {
  const running = sweep.sources.filter((source) => source.phase === "running");
  if (running.length > 1) return `Reading ${String(running.length)} sources`;
  const [only] = running;
  if (only) return `Reading ${sourceName(only)}`;
  return sweep.current_board ? `Reading ${readingName(sweep)}` : null;
}

/** What the sweep's sources are called: "website", or "source" once job feeds or company job
 * boards (mode "api", owner 2026-10-30) are among them. */
function sourceWord(sweep: Sweep): string {
  return sweep.sources.some((source) => source.mode === "api")
    ? "source"
    : "website";
}

/** The live line under the header while a sweep runs. A sweep searches the job websites ticked
 * in Find jobs alone (owner, 2026-10-28), so it counts them: one source with a host each, done
 * once its phase is. Below it, the live view of the website searched in the person's browser. */
export function SweepProgress({ runner }: { runner: SweepRunner }) {
  const sweep = runner.running;
  if (!sweep) return null;
  // The ticked job feeds and the company boards (mode "api") count with the websites: read
  // first, one source each. The alert e-mails (mode "mail") are not counted here.
  const websites = sweep.sources.filter(
    (source) => isWebsite(source) || isFeed(source) || isBoard(source),
  );
  const total = websites.length;
  const done = websites.filter((source) => source.phase === "done").length;
  const percent = total ? Math.round((100 * done) / total) : 0;
  const reading = readingLine(sweep);
  return (
    <section
      aria-label="Search in progress"
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 text-card-foreground"
    >
      <div
        role="status"
        aria-live="polite"
        className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
      >
        <span className="font-medium">
          {total
            ? `${String(done)} of ${plural(total, sourceWord(sweep))} searched`
            : reading
              ? "Searching job websites"
              : "Waiting for the worker"}
        </span>
        {reading ? (
          <span className="min-w-0 truncate text-muted-foreground">
            {reading}
          </span>
        ) : null}
        <span className="tabular-nums text-muted-foreground">
          {plural(sweep.new, "new posting")}
        </span>
      </div>
      {/* The bar is drawn from the percentage; it says the websites searched of all. */}
      <Progress
        value={percent}
        aria-label="Websites searched"
        aria-valuemax={total || 1}
        aria-valuenow={done}
        aria-valuetext={undefined}
        className="h-1.5 bg-muted [&>[data-slot=progress-indicator]]:rounded-full [&>[data-slot=progress-indicator]]:duration-500 [&>[data-slot=progress-indicator]]:motion-reduce:transition-none"
      />
      <SweepBrowsers sweep={sweep} />
    </section>
  );
}

/** The job feeds a sweep read (api app/jobs/feeds), as a part of its summary line. */
function feedsRead(sweep: Sweep): string[] {
  const feeds = sweep.sources.filter(
    (source) => source.mode === "api" && isJobFeed(source.kind),
  );
  return feeds.length ? [`${plural(feeds.length, "job feed")} read`] : [];
}

/** The company job boards a sweep read (api app/jobs/boards), as a part of its summary line. */
function boardsRead(sweep: Sweep): string[] {
  const boards = sweep.sources.filter(isBoard);
  return boards.length
    ? [`${plural(boards.length, "company board")} read`]
    : [];
}

/** What the sources of a sweep reported and the search did with it (jobs J30), added up: set
 * aside by the person's exclusions, refused by the app's check, opened by the app itself. */
function reportedTotals(sweep: Sweep): string[] {
  const sum = (count: (source: Sweep["sources"][number]) => number) =>
    sweep.sources.reduce((total, source) => total + count(source), 0);
  const unwanted = sum((source) => source.unwanted ?? 0);
  const refused = sum((source) =>
    Object.values(source.refused ?? {}).reduce((total, n) => total + n, 0),
  );
  const opened = sum((source) => source.opened_by_app ?? 0);
  const listedRead = sum((source) => source.listed_read ?? 0);
  const cards = sum((source) => countedCards(source.cards_refused));
  return [
    unwanted
      ? `${plural(unwanted, "posting")} set aside by your unwanted titles and places.`
      : "",
    refused
      ? `${plural(refused, "posting")} refused by the app's check: each source below says why.`
      : "",
    opened ? `The app opened ${plural(opened, "posting")} itself.` : "",
    listedReadSentence(listedRead),
    cards
      ? `${plural(cards, "card")} of a list refused by the app's check: each source below says why.`
      : "",
  ].filter((part) => part !== "");
}

/** The summary's sentences: what the sweep searched, then what it kept and why the rest was not
 * (jobs J30, in place of the postings "seen" and "matched", which counted only what was kept, for
 * every sweep, older ones included). */
function summaryLine(sweep: Sweep): string {
  const searched = [
    // A sweep that read no board (every sweep since the owner's decision of 2026-10-28, which
    // searches the ticked websites alone) counts the websites it searched instead.
    sweep.boards_total > 0
      ? `${plural(sweep.boards_ok, "board")} read`
      : `${plural(sweep.sources.filter(isWebsite).length, "website")} searched`,
    ...feedsRead(sweep),
    ...boardsRead(sweep),
  ];
  // Jobs J31: the sweep serves no total of the postings kept from a list; its sources do.
  const listed = sweep.sources.reduce(
    (total, source) => total + (source.listed ?? 0),
    0,
  );
  const kept = keptParts({ ...sweep, listed });
  return [
    `${searched.join(", ")}.`,
    kept.length ? `${capitalised(kept.join("; "))}.` : "No new posting kept.",
    ...reportedTotals(sweep),
  ]
    .filter((part) => part !== "")
    .join(" ");
}

const capitalised = (text: string) =>
  text.charAt(0).toUpperCase() + text.slice(1);

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
              ? "The search stopped"
              : sweep.new
                ? `${plural(sweep.new, "new posting")} found`
                : "No new posting"}
          </DialogTitle>
          <DialogDescription>
            {sweep.search ? `${sweep.search.name}: ` : ""}
            {summaryLine(sweep)}
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
                  <Item
                    asChild
                    size="sm"
                    className="flex-nowrap gap-3 rounded-none px-3 py-2 hover:bg-muted/60 focus-visible:bg-muted/60"
                  >
                    <Link
                      to={`/jobs/${job.id}`}
                      onClick={() => {
                        runner.showSummary(null);
                      }}
                    >
                      <ItemContent className="min-w-0 gap-0">
                        <ItemTitle className="block w-auto truncate">
                          {job.title}
                        </ItemTitle>
                        <ItemDescription className="line-clamp-1 text-xs break-all">
                          {job.company}
                          {job.location ? `, ${job.location}` : ""}
                        </ItemDescription>
                      </ItemContent>
                      <ItemActions>
                        {job.status === "skipped" ? (
                          <StatusBadge status={job.status} />
                        ) : null}
                        <FitBadge fit={job.fit} />
                      </ItemActions>
                    </Link>
                  </Item>
                </li>
              ))}
            </ul>
            {sweep.new > sweep.new_jobs.length ? (
              <p className="text-xs text-muted-foreground">
                The first {sweep.new_jobs.length} by fit. See them all in the
                list.
              </p>
            ) : null}
          </section>
        ) : null}
        <SweepSources sources={sweep.sources} />
        {sweep.sources.some(
          (source) => source.mode === "llm_browser" || source.mode === "web",
        ) ? (
          <SweepAssistantWords
            sweepId={sweep.id}
            onLeave={() => {
              runner.showSummary(null);
            }}
          />
        ) : null}
        {sweep.boards_failed.length ? (
          <section aria-label="Failed boards" className="flex flex-col gap-1">
            <h3 className="flex items-center gap-1.5 text-sm font-medium">
              <TriangleAlert
                aria-hidden="true"
                className="size-4 text-warning"
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
          {sweep.new ? (
            <Button variant="outline" asChild>
              <Link
                to={`/jobs?sweep=${encodeURIComponent(sweep.id)}`}
                onClick={() => {
                  runner.showSummary(null);
                }}
              >
                See them all in the list
              </Link>
            </Button>
          ) : null}
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

/** The last sweeps and the automatic schedule, in a dialog: "Past searches", opened from the
 * page's More actions menu. */
export function PastSearchesDialog({
  open,
  onOpenChange,
  runner,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  runner: SweepRunner;
}) {
  const [opening, setOpening] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const history = useSweeps({ enabled: open });
  const fetchSweep = useFetchSweep();
  const schedule = history.data?.schedule;

  const show = (id: string) => {
    setOpening(id);
    fetchSweep(id).then(
      (detail) => {
        setOpening(null);
        onOpenChange(false);
        runner.showSummary(detail);
      },
      (error: unknown) => {
        setOpening(null);
        toast.error(`This search could not be opened. ${describeError(error)}`);
      },
    );
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg [&>*]:min-w-0">
          <DialogHeader>
            <DialogTitle>Past searches</DialogTitle>
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
                The past searches could not be loaded.{" "}
                {describeError(history.error)}
              </AlertDescription>
            </Alert>
          ) : history.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No search yet. Find jobs searches every place on your list.
            </p>
          ) : (
            <ul
              aria-label="Last searches"
              className="flex flex-col divide-y rounded-md border"
            >
              {history.data.items.map((sweep) => (
                <li key={sweep.id}>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={sweep.status === "running" || opening !== null}
                    onClick={() => {
                      show(sweep.id);
                    }}
                    className="h-auto w-full justify-start gap-3 rounded-none px-3 py-2 text-left font-normal whitespace-normal hover:bg-muted/60 hover:text-foreground focus-visible:bg-muted/60 disabled:cursor-default disabled:opacity-100 dark:hover:bg-muted/60"
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="font-medium">
                        {formatDateTime(sweep.started_at)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {sweep.search?.name ?? "Main search"},{" "}
                        {sweep.trigger === "schedule" ? "automatic" : "by hand"}
                        , {STATUS_TEXT[sweep.status] ?? sweep.status}
                        {sweep.boards_failed.length
                          ? `, ${plural(sweep.boards_failed.length, "board")} failed`
                          : ""}
                      </span>
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      {opening === sweep.id ? (
                        <Spinner aria-label="Opening" />
                      ) : (
                        `${sweep.new} new`
                      )}
                    </span>
                  </Button>
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
