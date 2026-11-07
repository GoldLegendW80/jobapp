import { X } from "lucide-react";

import { Button } from "@/components/ui/button";

import { activeFilterChips } from "@/features/jobs/helpers/filter-chips";
import {
  EMPTY_FILTERS,
  hasFilters,
  type FilterProps,
} from "@/features/jobs/helpers/filters";

/** One chip per active filter, so the view is readable at a glance and each part removable. */
export function FilterChips({ filters, onChange }: FilterProps) {
  if (!hasFilters(filters)) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ul aria-label="Active filters" className="flex min-w-0 flex-wrap gap-2">
        {activeFilterChips(filters).map((chip) => (
          <li
            key={chip.key}
            className="inline-flex max-w-full items-center gap-1 rounded-full border bg-secondary py-0.5 pr-1 pl-3 text-xs text-secondary-foreground"
          >
            <span className="truncate">{chip.text}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Remove filter ${chip.text}`}
              onClick={() => {
                onChange(chip.without);
              }}
              className="size-5 rounded-full"
            >
              <X aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
      <Button
        variant="ghost"
        size="sm"
        className="h-7"
        onClick={() => {
          onChange({ ...EMPTY_FILTERS, sort: filters.sort });
        }}
      >
        Clear filters
      </Button>
    </div>
  );
}
