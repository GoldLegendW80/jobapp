import type { FilterProps } from "@/features/jobs/helpers/filters";
import { CompanyField } from "@/components/shared/jobs/filters/company-field";
import { DateRange } from "@/components/shared/jobs/filters/date-range";
import { FilterChips } from "@/components/shared/jobs/filters/filter-chips";
import { FitRange } from "@/components/shared/jobs/filters/fit-range";
import { SearchField } from "@/components/shared/jobs/filters/search-field";
import { SortField } from "@/components/shared/jobs/filters/sort-field";
import { StatusMenu } from "@/components/shared/jobs/filters/status-menu";
import { TypeMenu } from "@/components/shared/jobs/filters/type-menu";

/**
 * Search, status, type, company, fit range, found dates and sort, then one removable chip per
 * active filter and "Clear filters", in one card (the map's). Text fields commit after a pause in
 * typing; the others at once. Every value comes from and goes to the URL through `filters` and
 * `onChange`. The Jobs list splits the same parts into a toolbar and a sheet (filters-toolbar.tsx).
 */
export function FilterBar({ filters, onChange }: FilterProps) {
  return (
    <section
      aria-label="Filters"
      className="flex flex-col gap-3 rounded-lg border bg-card p-3"
    >
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <SearchField
          filters={filters}
          onChange={onChange}
          className="sm:col-span-full"
        />
        <StatusMenu filters={filters} onChange={onChange} />
        <TypeMenu filters={filters} onChange={onChange} />
        <CompanyField filters={filters} onChange={onChange} />
        <SortField filters={filters} onChange={onChange} />
      </div>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <FitRange filters={filters} onChange={onChange} />
        <DateRange filters={filters} onChange={onChange} />
      </div>
      <FilterChips filters={filters} onChange={onChange} />
    </section>
  );
}
