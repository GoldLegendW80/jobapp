import {
  CalendarClock,
  ChartNoAxesColumn,
  EllipsisVertical,
  History,
  RefreshCw,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import type { ScheduleState } from "@/features/jobs/api/sweeps-api";
import { scheduleLine, useSchedule } from "@/features/jobs/api/use-schedule";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { PastSearchesDialog } from "../find-jobs";
import { ScheduleDialog } from "../schedule-dialog";
import { RescoreDialog } from "./rescore-dialog";

type Opened = "schedule" | "history" | "rescore-all" | "rescore-selected";

/** The schedule's line without its name, which the item already says: "Off.", or "On, weekdays
 * at 07:30 (Europe/Paris). Next: ...". */
function scheduleState(state: ScheduleState | undefined): string {
  if (!state) return "Loading";
  const line = scheduleLine(state).replace(/^Automatic search: /, "");
  return `${line.charAt(0).toUpperCase()}${line.slice(1)}`;
}

/**
 * The page's "More actions" menu: the automatic search (its state on the item), the past
 * searches, and rescoring all postings or the selected ones. Each opens its own dialog; Your
 * search in numbers opens its page.
 */
export function PageMenu({
  selected,
  runner,
}: {
  selected: string[];
  runner: SweepRunner;
}) {
  const [opened, setOpened] = useState<Opened | null>(null);
  const schedule = useSchedule();
  const close = () => {
    setOpened(null);
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="More actions">
            <EllipsisVertical aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuItem
            className="items-start"
            onSelect={() => {
              setOpened("schedule");
            }}
          >
            <CalendarClock aria-hidden="true" className="mt-0.5" />
            <span className="flex min-w-0 flex-col">
              Automatic search
              <span className="text-xs text-muted-foreground">
                {scheduleState(schedule)}
              </span>
            </span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setOpened("history");
            }}
          >
            <History aria-hidden="true" />
            Past searches
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link to="/jobs/insights">
              <ChartNoAxesColumn aria-hidden="true" />
              Your search in numbers
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              setOpened("rescore-all");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore all postings
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={selected.length === 0}
            onSelect={() => {
              setOpened("rescore-selected");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore the selected postings
            {selected.length ? ` (${selected.length})` : ""}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {opened === "schedule" ? (
        <ScheduleDialog state={schedule} onClose={close} />
      ) : null}
      <PastSearchesDialog
        open={opened === "history"}
        onOpenChange={(open) => {
          setOpened(open ? "history" : null);
        }}
        runner={runner}
      />
      {opened === "rescore-all" || opened === "rescore-selected" ? (
        <RescoreDialog
          ids={opened === "rescore-selected" ? selected : undefined}
          onClose={close}
        />
      ) : null}
    </>
  );
}
