import { SlidersHorizontal } from "lucide-react";
import { lazy, Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

import type { FitBucket } from "@/features/jobs/api/jobs-api";
import {
  activeFilterCount,
  EMPTY_FILTERS,
  hasFilters,
  toggleFitRange,
  type JobFilters,
} from "@/features/jobs/helpers/filters";
import { plural } from "@/features/jobs/helpers/selection";
import { CompanyField } from "./company-field";
import { FilterChips } from "./filter-chips";
import { FitRange } from "./fit-range";
import { SearchField } from "./search-field";
import { SortField } from "./sort-field";
import { StatusFilter } from "./status-filter";
import { StatusMenu } from "./status-menu";
import { TypeMenu } from "./type-menu";

// Recharts (the chart) and react-day-picker (the calendar) are heavy: they load when the Filters
// sheet first opens, not with the page.
const FitHistogram = lazy(() =>
  import("./fit-histogram").then((module) => ({
    default: module.FitHistogram,
  })),
);
const DateRange = lazy(() =>
  import("./date-range").then((module) => ({ default: module.DateRange })),
);

/** What the list api says of the current filters: the counts, the fit spread and the total. */
export interface FiltersSummary {
  counts_by_status: Record<string, number>;
  fit_histogram: { buckets: FitBucket[]; unscored: number };
  total: number;
}

/**
 * The toolbar of both Jobs views, List and Map: the search box, a Filters button, the status
 * toggles with their counts, then one chip per active filter. The button counts the filters set
 * in its sheet (the search shows itself) and opens them: status, type, company, sort, the fit
 * range with its histogram, and the found dates. Every change applies at once, as the URL
 * changes; the sheet's last button only closes it, naming how many postings the view now holds.
 */
export function FiltersToolbar({
  filters,
  onChange,
  summary,
  unavailable = false,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
  /** Undefined while the first answer loads. */
  summary: FiltersSummary | undefined;
  /** The counts could not be loaded. */
  unavailable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const set = activeFilterCount({ ...filters, q: "" });
  const histogram = summary?.fit_histogram;
  return (
    <div
      role="search"
      aria-label="Filter postings"
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex w-full items-center gap-2 sm:w-auto">
          <SearchField
            filters={filters}
            onChange={onChange}
            className="min-w-0 flex-1 bg-card sm:w-80 sm:flex-none"
          />
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="outline">
                <SlidersHorizontal aria-hidden="true" />
                Filters
                {set ? (
                  <>
                    <span
                      aria-hidden="true"
                      className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground tabular-nums"
                    >
                      {set}
                    </span>
                    <span className="sr-only">, {set} set</span>
                  </>
                ) : null}
              </Button>
            </SheetTrigger>
            <SheetContent className="w-full gap-0 bg-card sm:max-w-md">
              <SheetHeader className="border-b">
                <SheetTitle>Filters</SheetTitle>
                <SheetDescription>
                  Each change shows in the view at once.
                </SheetDescription>
              </SheetHeader>
              <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
                <StatusMenu filters={filters} onChange={onChange} />
                <TypeMenu filters={filters} onChange={onChange} />
                <CompanyField filters={filters} onChange={onChange} labelled />
                <SortField filters={filters} onChange={onChange} />
                <div className="flex flex-col gap-3">
                  <FitRange filters={filters} onChange={onChange} />
                  <Suspense
                    fallback={<Skeleton className="h-52 w-full rounded-lg" />}
                  >
                    <FitHistogram
                      buckets={histogram?.buckets ?? EMPTY_BUCKETS}
                      unscored={histogram?.unscored ?? 0}
                      fitMin={filters.fitMin}
                      fitMax={filters.fitMax}
                      loading={!histogram}
                      onSelect={(low, high) => {
                        onChange(toggleFitRange(filters, low, high));
                      }}
                    />
                  </Suspense>
                </div>
                <Suspense
                  fallback={<Skeleton className="h-16 w-80 rounded-lg" />}
                >
                  <DateRange filters={filters} onChange={onChange} />
                </Suspense>
              </div>
              <SheetFooter className="flex-row justify-end border-t">
                {hasFilters(filters) ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      onChange({ ...EMPTY_FILTERS, sort: filters.sort });
                    }}
                  >
                    Clear filters
                  </Button>
                ) : null}
                <SheetClose asChild>
                  <Button>
                    {summary
                      ? `Show ${plural(summary.total, "posting")}`
                      : "Show postings"}
                  </Button>
                </SheetClose>
              </SheetFooter>
            </SheetContent>
          </Sheet>
        </div>
        {/* A phone: one row that scrolls sideways, edge to edge (the shell's padding is 1rem). */}
        <div className="-mx-4 w-[calc(100%+2rem)] overflow-x-auto px-4 [scrollbar-width:none] sm:mx-0 sm:w-auto sm:max-w-full sm:px-0">
          <StatusFilter
            filters={filters}
            onChange={onChange}
            counts={summary?.counts_by_status}
            loading={!summary && !unavailable}
            unavailable={unavailable}
          />
        </div>
      </div>
      <FilterChips filters={filters} onChange={onChange} />
    </div>
  );
}

const EMPTY_BUCKETS: FitBucket[] = Array.from({ length: 10 }, (_, i) => ({
  low: i * 10,
  high: i === 9 ? 100 : i * 10 + 9,
  count: 0,
}));
