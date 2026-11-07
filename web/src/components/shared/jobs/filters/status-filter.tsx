import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import { statusCounts } from "@/features/jobs/api/jobs-api";
import {
  toggleOnlyStatus,
  type JobFilters,
} from "@/features/jobs/helpers/filters";
import { STATUS_LABELS, type JobStatus } from "@/features/jobs/helpers/labels";

/**
 * One toggle per status in pipeline order, with its count (every filter but the status one, so
 * the counts stay useful while a status is picked). Picking a status shows it alone; picking it
 * again shows them all. While the counts load they pulse; when they could not be loaded the
 * toggles say so in their names and still filter.
 */
export function StatusFilter({
  filters,
  onChange,
  counts,
  loading,
  unavailable = false,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
  counts: Record<string, number> | undefined;
  loading: boolean;
  /** The counts' request failed: there is no count to show. */
  unavailable?: boolean;
}) {
  return (
    <ToggleGroup
      type="multiple"
      variant="outline"
      size="sm"
      aria-label="Postings per status"
      value={filters.statuses}
      onValueChange={(next) => {
        // Radix reports the new set; the status clicked is the one that came or went.
        const clicked =
          next.find(
            (status) => !filters.statuses.includes(status as JobStatus),
          ) ?? filters.statuses.find((status) => !next.includes(status));
        if (clicked) onChange(toggleOnlyStatus(filters, clicked as JobStatus));
      }}
    >
      {statusCounts(counts).map(({ status, count }) => {
        const value = unavailable
          ? "unavailable"
          : loading
            ? "loading"
            : count.toLocaleString("en-GB");
        return (
          <ToggleGroupItem
            key={status}
            value={status}
            aria-label={`${STATUS_LABELS[status]}: ${value}`}
            className="gap-1.5 bg-card px-3"
          >
            {STATUS_LABELS[status]}
            {unavailable ? null : loading ? (
              <Skeleton
                aria-hidden="true"
                className="h-3 w-4 rounded bg-muted"
              />
            ) : (
              <span className="tabular-nums opacity-60">{value}</span>
            )}
          </ToggleGroupItem>
        );
      })}
    </ToggleGroup>
  );
}
