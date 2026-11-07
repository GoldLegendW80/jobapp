import { Skeleton } from "@/components/ui/skeleton";

/** Placeholder items while the first page loads. */
export function JobsListSkeleton({ items = 6 }: { items?: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading postings"
      className="flex flex-col gap-1 rounded-xl bg-card p-2"
    >
      {Array.from({ length: items }, (_, index) => (
        <div key={index} aria-hidden="true" className="flex gap-3 p-3">
          <Skeleton className="size-10 shrink-0 rounded-full bg-muted" />
          <div className="flex flex-1 flex-col gap-2 pt-1">
            <Skeleton className="h-3.5 w-3/4 rounded bg-muted" />
            <Skeleton className="h-3 w-1/3 rounded bg-muted" />
            <Skeleton className="h-3 w-1/2 rounded bg-muted" />
            <Skeleton className="mt-1 h-5 w-28 rounded-full bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
