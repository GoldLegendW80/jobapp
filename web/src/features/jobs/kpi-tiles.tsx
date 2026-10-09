import { cn } from "@/lib/utils";

import { statusCounts } from "./api";
import { STATUS_LABELS, statusTone, type JobStatus } from "./labels";

/**
 * One tile per status in pipeline order with its count (every filter but the status one, so the
 * tiles stay useful while a status is selected). A click shows that status alone; a second click
 * on the same tile shows them all again.
 */
export function KpiTiles({
  counts,
  selected,
  onSelect,
  loading,
}: {
  counts: Record<string, number> | undefined;
  selected: readonly JobStatus[];
  onSelect: (status: JobStatus) => void;
  loading: boolean;
}) {
  return (
    <section aria-labelledby="kpi-title">
      <h2 id="kpi-title" className="sr-only">
        Postings per status
      </h2>
      <ul className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9">
        {statusCounts(counts).map(({ status, count }) => {
          const pressed = selected.includes(status);
          return (
            <li key={status}>
              <button
                type="button"
                aria-pressed={pressed}
                aria-label={`${STATUS_LABELS[status]}: ${loading ? "loading" : count}`}
                onClick={() => {
                  onSelect(status);
                }}
                className={cn(
                  "flex w-full flex-col items-start gap-1 rounded-lg border bg-card px-3 py-2 text-left transition-colors hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                  pressed && "border-primary ring-1 ring-primary",
                )}
              >
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    aria-hidden="true"
                    className={cn(
                      "size-2 rounded-full border",
                      statusTone(status),
                    )}
                  />
                  <span className="truncate">{STATUS_LABELS[status]}</span>
                </span>
                {loading ? (
                  <span
                    aria-hidden="true"
                    className="my-1 h-5 w-10 animate-pulse rounded bg-muted"
                  />
                ) : (
                  <span className="text-xl font-semibold tabular-nums">
                    {count}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
