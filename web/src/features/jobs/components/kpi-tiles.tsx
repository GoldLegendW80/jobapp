import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { statusCounts } from "../api/jobs-api";
import { STATUS_LABELS, statusTone, type JobStatus } from "../helpers/labels";

/**
 * One tile per status in pipeline order with its count (every filter but the status one, so the
 * tiles stay useful while a status is selected). A click shows that status alone; a second click
 * on the same tile shows them all again. On a phone the tiles are one row that scrolls sideways.
 * While the list loads they pulse; when it could not be loaded they say so instead of pulsing.
 */
export function KpiTiles({
  counts,
  selected,
  onSelect,
  loading,
  unavailable = false,
}: {
  counts: Record<string, number> | undefined;
  selected: readonly JobStatus[];
  onSelect: (status: JobStatus) => void;
  loading: boolean;
  /** The list request failed: there is no count to show. */
  unavailable?: boolean;
}) {
  return (
    <section aria-labelledby="kpi-title">
      <h2 id="kpi-title" className="sr-only">
        Postings per status
      </h2>
      {/* A phone: one row, scrolled sideways, edge to edge (the page's own padding is 1rem). */}
      <ul className="-mx-4 flex snap-x scroll-px-4 gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:grid sm:grid-cols-5 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-9">
        {statusCounts(counts).map(({ status, count }) => {
          const pressed = selected.includes(status);
          const value = unavailable
            ? "unavailable"
            : loading
              ? "loading"
              : count.toLocaleString("en-GB");
          return (
            <li key={status} className="w-28 shrink-0 snap-start sm:w-auto">
              <Button
                type="button"
                variant="outline"
                aria-pressed={pressed}
                aria-label={`${STATUS_LABELS[status]}: ${value}`}
                onClick={() => {
                  onSelect(status);
                }}
                className={cn(
                  "h-auto w-full flex-col items-start gap-1 rounded-lg bg-card px-3 py-2 text-left font-normal whitespace-normal shadow-none transition-colors hover:text-foreground dark:border-border dark:bg-card dark:hover:bg-accent",
                  pressed &&
                    "border-primary ring-1 ring-primary dark:border-primary",
                )}
              >
                <span className="flex max-w-full items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    aria-hidden="true"
                    className={cn(
                      "size-2 shrink-0 rounded-full border",
                      statusTone(status),
                    )}
                  />
                  <span className="truncate">{STATUS_LABELS[status]}</span>
                </span>
                {unavailable ? (
                  <span className="flex h-7 items-center text-sm text-muted-foreground">
                    Unavailable
                  </span>
                ) : loading ? (
                  <Skeleton
                    aria-hidden="true"
                    className="my-1 h-5 w-10 rounded bg-muted"
                  />
                ) : (
                  <span className="text-xl font-semibold tabular-nums">
                    {value}
                  </span>
                )}
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
