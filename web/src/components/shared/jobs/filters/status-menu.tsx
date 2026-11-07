import type { FilterProps } from "@/features/jobs/helpers/filters";
import { STATUS_LABELS, STATUSES } from "@/features/jobs/helpers/labels";
import { MultiSelectMenu } from "./multi-select-menu";

/** The statuses to show, in pipeline order. */
export function StatusMenu({ filters, onChange }: FilterProps) {
  return (
    <MultiSelectMenu
      label="Status"
      options={STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
      selected={filters.statuses}
      onChange={(statuses) => {
        onChange({
          ...filters,
          statuses: STATUSES.filter((s) => statuses.includes(s)),
        });
      }}
    />
  );
}
