import { Plus } from "lucide-react";
import { useFieldArray, type Control } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  SALARY_RANGES_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  Group,
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";
import { ChoiceField, type Choice } from "./choice-field";

const STANCES: readonly Choice[] = [
  { value: "", label: "Automatic" },
  { value: "leave_blank", label: "Leave it blank" },
  { value: "range", label: "Give my range" },
  { value: "sentence", label: "Write my salary policy" },
];

const PERIODS: readonly Choice[] = [
  { value: "year", label: "Year" },
  { value: "month", label: "Month" },
  { value: "day", label: "Day" },
];

/** What forms get when they ask for a salary: the stance, and the ranges per currency. */
export function SalaryGroup({ control }: { control: Control<AnswersForm> }) {
  const ranges = useFieldArray({ control, name: "salary_ranges" });
  return (
    <Group
      title="Salary"
      description="What forms get when they ask for a salary. Never a figure you did not write."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={ranges.fields.length >= SALARY_RANGES_MAX}
          onClick={() => {
            ranges.append(
              { currency: "", minimum: "", maximum: "", period: "year" },
              { shouldFocus: true },
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add range
        </Button>
      }
    >
      <ChoiceField
        control={control}
        name="salary_stance"
        label="Salary stance"
        description="Automatic uses the salary policy sentence when it is filled and leaves the field blank otherwise."
        options={STANCES}
      />
      {ranges.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No range yet.</p>
      ) : (
        <ol className="flex flex-col gap-4">
          {ranges.fields.map((item, index) => {
            const n = index + 1;
            return (
              <li key={item.id} className="rounded-md border p-3 sm:p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h4 className="text-sm font-medium">Range {n}</h4>
                  <ItemControls
                    index={index}
                    count={ranges.fields.length}
                    name={`range ${n}`}
                    onMove={ranges.move}
                    onRemove={ranges.remove}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    control={control}
                    name={`salary_ranges.${index}.currency`}
                    label="Currency"
                    srSuffix={`of range ${n}`}
                    placeholder="EUR"
                    maxLength={3}
                  />
                  <ChoiceField
                    control={control}
                    name={`salary_ranges.${index}.period`}
                    label="Per"
                    srSuffix={`of range ${n}`}
                    options={PERIODS}
                  />
                  <TextField
                    control={control}
                    name={`salary_ranges.${index}.minimum`}
                    label="Minimum"
                    srSuffix={`of range ${n}`}
                    inputMode="numeric"
                  />
                  <TextField
                    control={control}
                    name={`salary_ranges.${index}.maximum`}
                    label="Maximum"
                    srSuffix={`of range ${n}`}
                    inputMode="numeric"
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Group>
  );
}
