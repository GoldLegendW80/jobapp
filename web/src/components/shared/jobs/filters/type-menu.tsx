import type { FilterProps } from "@/features/jobs/helpers/filters";
import { CONTRACT_TYPES } from "@/features/jobs/helpers/labels";
import { MultiSelectMenu } from "./multi-select-menu";

/** The contract types to show. */
export function TypeMenu({ filters, onChange }: FilterProps) {
  return (
    <MultiSelectMenu
      label="Type"
      options={CONTRACT_TYPES.map((t) => ({ value: t, label: t }))}
      selected={filters.types}
      onChange={(types) => {
        onChange({
          ...filters,
          types: CONTRACT_TYPES.filter((t) => types.includes(t)),
        });
      }}
    />
  );
}
