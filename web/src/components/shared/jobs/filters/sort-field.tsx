import { useId } from "react";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";

import type { FilterProps } from "@/features/jobs/helpers/filters";
import { isSort, SORT_LABELS, SORTS } from "@/features/jobs/helpers/labels";

/** The order of the list. */
export function SortField({ filters, onChange }: FilterProps) {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor={id} className="shrink-0 text-sm text-muted-foreground">
        Sort
      </Label>
      <NativeSelect
        id={id}
        value={filters.sort}
        onChange={(event) => {
          const value = event.target.value;
          if (isSort(value)) onChange({ ...filters, sort: value });
        }}
      >
        {SORTS.map((sort) => (
          <option key={sort} value={sort}>
            {SORT_LABELS[sort]}
          </option>
        ))}
      </NativeSelect>
    </div>
  );
}
