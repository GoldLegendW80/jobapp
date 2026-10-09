import { cn } from "@/lib/utils";

import type { FitBucket } from "./api";
import { bucketTone } from "./labels";

/**
 * Fit distribution of the filtered rows: ten buckets (0 to 9 ... 90 to 100) drawn as bars. Each
 * bar is a button that narrows the list to its range (a second click clears it), named for
 * screen readers with its range and count. Unscored rows are counted beside the chart; the api
 * cannot filter on them, so that count is not a button.
 */
export function FitHistogram({
  buckets,
  unscored,
  fitMin,
  fitMax,
  onSelect,
  loading,
}: {
  buckets: FitBucket[];
  unscored: number;
  fitMin: number | null;
  fitMax: number | null;
  onSelect: (low: number, high: number) => void;
  loading: boolean;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const rangeActive = fitMin !== null || fitMax !== null;
  return (
    <section
      aria-labelledby="fit-title"
      className="flex flex-col gap-2 rounded-lg border bg-card p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="fit-title" className="text-sm font-medium">
          Fit distribution
        </h2>
        <p className="text-xs text-muted-foreground">{unscored} unscored</p>
      </div>
      <ul
        aria-label="Postings per fit range"
        aria-busy={loading}
        className={cn(
          "grid h-28 grid-cols-10 items-end gap-1",
          loading && "opacity-50",
        )}
      >
        {buckets.map((bucket) => {
          const pressed = fitMin === bucket.low && fitMax === bucket.high;
          const height =
            bucket.count === 0 ? 0 : Math.max(4, (bucket.count / max) * 100);
          return (
            <li key={bucket.low} className="flex h-full flex-col">
              <button
                type="button"
                aria-pressed={pressed}
                aria-label={`Fit ${bucket.low} to ${bucket.high}: ${bucket.count} postings`}
                title={`Fit ${bucket.low} to ${bucket.high}: ${bucket.count}`}
                onClick={() => {
                  onSelect(bucket.low, bucket.high);
                }}
                className="group flex h-full w-full flex-col items-center justify-end gap-1 rounded-sm focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <span
                  aria-hidden="true"
                  className="text-[10px] leading-none text-muted-foreground tabular-nums"
                >
                  {bucket.count}
                </span>
                <span
                  aria-hidden="true"
                  style={{ height: `${height}%` }}
                  className={cn(
                    "w-full rounded-t-sm transition-opacity group-hover:opacity-80",
                    bucketTone(bucket.low),
                    rangeActive && !pressed && "opacity-35",
                    pressed &&
                      "ring-2 ring-primary ring-offset-1 ring-offset-card",
                  )}
                />
              </button>
            </li>
          );
        })}
      </ul>
      <div
        aria-hidden="true"
        className="grid grid-cols-10 gap-1 text-center text-[10px] text-muted-foreground tabular-nums"
      >
        {buckets.map((bucket) => (
          <span key={bucket.low}>{bucket.low}</span>
        ))}
      </div>
    </section>
  );
}
