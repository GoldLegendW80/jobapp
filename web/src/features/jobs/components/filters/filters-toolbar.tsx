import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";

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

import type { FitBucket } from "../../api/jobs-api";
import {
  activeFilterCount,
  EMPTY_FILTERS,
  hasFilters,
  toggleFitRange,
  type JobFilters,
} from "@/features/jobs/helpers/filters";
import { plural } from "@/features/jobs/helpers/selection";
import { FitHistogram } from "../fit-histogram";
import { CompanyField } from "@/components/shared/jobs/filters/company-field";
import { DateRange } from "@/components/shared/jobs/filters/date-range";
import { FilterChips } from "@/components/shared/jobs/filters/filter-chips";
import { FitRange } from "@/components/shared/jobs/filters/fit-range";
import { SearchField } from "@/components/shared/jobs/filters/search-field";
import { SortField } from "@/components/shared/jobs/filters/sort-field";
import { StatusMenu } from "@/components/shared/jobs/filters/status-menu";
import { TypeMenu } from "@/components/shared/jobs/filters/type-menu";

/**
 * The Jobs list's toolbar: the search box and a Filters button, as wide as the list under it,
 * then one chip per active filter. The button counts the filters set in its sheet (the search
 * shows itself) and opens them: status, type, company, sort, the fit range with its histogram,
 * and the found dates. Every change applies at once, as the URL changes; the sheet's last button
 * only closes it, naming how many postings the list now holds.
 */
export function FiltersToolbar({
  filters,
  onChange,
  total,
  histogram,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
  /** The postings of the current view, null while the first page loads. */
  total: number | null;
  histogram: { buckets: FitBucket[]; unscored: number } | undefined;
}) {
  const [open, setOpen] = useState(false);
  const set = activeFilterCount({ ...filters, q: "" });
  return (
    <div
      role="search"
      aria-label="Filter postings"
      className="flex flex-wrap items-center gap-x-4 gap-y-3"
    >
      <div className="flex w-full items-center gap-2 lg:w-[26rem]">
        <SearchField
          filters={filters}
          onChange={onChange}
          className="min-w-0 flex-1 [&_input]:bg-card"
        />
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger asChild>
            <Button variant="surface">
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
                Each change shows in the list at once.
              </SheetDescription>
            </SheetHeader>
            <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
              <StatusMenu filters={filters} onChange={onChange} />
              <TypeMenu filters={filters} onChange={onChange} />
              <CompanyField filters={filters} onChange={onChange} labelled />
              <SortField filters={filters} onChange={onChange} />
              <div className="flex flex-col gap-3">
                <FitRange filters={filters} onChange={onChange} />
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
              </div>
              <DateRange filters={filters} onChange={onChange} />
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
                  {total === null
                    ? "Show postings"
                    : `Show ${plural(total, "posting")}`}
                </Button>
              </SheetClose>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      </div>
      <div className="min-w-0 flex-1 empty:hidden">
        <FilterChips filters={filters} onChange={onChange} />
      </div>
    </div>
  );
}

const EMPTY_BUCKETS: FitBucket[] = Array.from({ length: 10 }, (_, i) => ({
  low: i * 10,
  high: i === 9 ? 100 : i * 10 + 9,
  count: 0,
}));
