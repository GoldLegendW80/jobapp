import { useId, useState } from "react";

import {
  Field,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";

import {
  parseFitInput,
  type FilterProps,
} from "@/features/jobs/helpers/filters";

/** Two numbers committed on Enter or when focus leaves, only as a valid range. */
export function FitRange({ filters, onChange }: FilterProps) {
  const ids = useId();
  const [min, setMin] = useState(filters.fitMin?.toString() ?? "");
  const [max, setMax] = useState(filters.fitMax?.toString() ?? "");
  const [error, setError] = useState<string>();
  // A range changed elsewhere (the histogram, a chip, back) refills the fields, without
  // remounting them, so focus stays where it is.
  const [shown, setShown] = useState([filters.fitMin, filters.fitMax]);
  if (shown[0] !== filters.fitMin || shown[1] !== filters.fitMax) {
    setShown([filters.fitMin, filters.fitMax]);
    setMin(filters.fitMin?.toString() ?? "");
    setMax(filters.fitMax?.toString() ?? "");
    setError(undefined);
  }

  const commit = () => {
    const low = parseFitInput(min);
    const high = parseFitInput(max);
    if (low === "invalid" || high === "invalid") {
      setError("Fit is a whole number from 0 to 100.");
      return;
    }
    if (low !== null && high !== null && low > high) {
      setError("The lowest fit is above the highest.");
      return;
    }
    setError(undefined);
    if (low !== filters.fitMin || high !== filters.fitMax) {
      onChange({ ...filters, fitMin: low, fitMax: high });
    }
  };

  const errorId = error ? `${ids}-error` : undefined;
  return (
    <FieldSet className="gap-1">
      <FieldLegend className="sr-only">Fit range</FieldLegend>
      <div className="flex items-end gap-2">
        <FitInput
          id={`${ids}-min`}
          label="Fit from"
          value={min}
          errorId={errorId}
          onChange={setMin}
          onCommit={commit}
        />
        <FitInput
          id={`${ids}-max`}
          label="Fit to"
          value={max}
          errorId={errorId}
          onChange={setMax}
          onCommit={commit}
        />
      </div>
      {error ? (
        <FieldError id={errorId} className="text-xs">
          {error}
        </FieldError>
      ) : null}
    </FieldSet>
  );
}

/** One end of the range; `errorId` names the range's problem while there is one. */
function FitInput({
  id,
  label,
  value,
  errorId,
  onChange,
  onCommit,
}: {
  id: string;
  label: string;
  value: string;
  errorId: string | undefined;
  onChange: (value: string) => void;
  onCommit: () => void;
}) {
  return (
    <Field className="w-20 gap-1">
      <FieldLabel htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </FieldLabel>
      <Input
        id={id}
        inputMode="numeric"
        className="w-20"
        value={value}
        aria-invalid={errorId ? true : undefined}
        aria-describedby={errorId}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        onBlur={onCommit}
        onKeyDown={(event) => {
          if (event.key === "Enter") onCommit();
        }}
      />
    </Field>
  );
}
