import { useId, useState } from "react";

import {
  Field,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";

import { isIsoDate, type FilterProps } from "@/features/jobs/helpers/filters";

/** Found between two dates, committed as soon as the pair is valid. */
export function DateRange({ filters, onChange }: FilterProps) {
  const ids = useId();
  const [error, setError] = useState<string>();

  const set = (key: "foundFrom" | "foundTo", raw: string) => {
    const value = raw === "" ? null : raw;
    if (value !== null && !isIsoDate(value)) return;
    const next = { ...filters, [key]: value };
    if (next.foundFrom && next.foundTo && next.foundFrom > next.foundTo) {
      setError("The first date is after the second.");
      return;
    }
    setError(undefined);
    onChange(next);
  };

  return (
    <FieldSet className="gap-1">
      <FieldLegend className="sr-only">Found between</FieldLegend>
      <div className="flex items-end gap-2">
        <Field className="w-40 gap-1">
          <FieldLabel
            htmlFor={`${ids}-from`}
            className="text-xs text-muted-foreground"
          >
            Found from
          </FieldLabel>
          <Input
            id={`${ids}-from`}
            type="date"
            className="w-40"
            value={filters.foundFrom ?? ""}
            onChange={(event) => {
              set("foundFrom", event.target.value);
            }}
          />
        </Field>
        <Field className="w-40 gap-1">
          <FieldLabel
            htmlFor={`${ids}-to`}
            className="text-xs text-muted-foreground"
          >
            Found to
          </FieldLabel>
          <Input
            id={`${ids}-to`}
            type="date"
            className="w-40"
            value={filters.foundTo ?? ""}
            onChange={(event) => {
              set("foundTo", event.target.value);
            }}
          />
        </Field>
      </div>
      {error ? <FieldError className="text-xs">{error}</FieldError> : null}
    </FieldSet>
  );
}
