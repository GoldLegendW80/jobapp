import { useId } from "react";
import type { Control, FieldPath } from "react-hook-form";

import { FieldTitle } from "@/components/ui/field";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import type { Choice } from "./choices";

/** A choice of the form whose value is "" (not said, automatic): a toggle needs a value. */
const UNSET = "unset";

/** One choice among a few, as a row of toggles: exactly one stays on. */
export function ChoiceToggles({
  control,
  name,
  label,
  srSuffix,
  description,
  options,
}: {
  control: Control<AnswersForm>;
  name: FieldPath<AnswersForm>;
  label: string;
  srSuffix?: string;
  description?: string;
  options: readonly Choice[];
}) {
  const labelId = useId();
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => {
        const value = typeof field.value === "string" ? field.value : "";
        return (
          <FormItem>
            <FieldTitle id={labelId}>{label}</FieldTitle>
            {srSuffix ? (
              <span id={`${labelId}-suffix`} hidden>
                {srSuffix}
              </span>
            ) : null}
            <FormControl>
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                spacing={2}
                aria-labelledby={
                  srSuffix ? `${labelId} ${labelId}-suffix` : labelId
                }
                className="flex-wrap"
                value={value === "" ? UNSET : value}
                onValueChange={(next) => {
                  // A click on the toggle that is on would leave none on: it stays.
                  if (next) field.onChange(next === UNSET ? "" : next);
                }}
              >
                {options.map((option) => (
                  <ToggleGroupItem
                    key={option.value}
                    value={option.value === "" ? UNSET : option.value}
                  >
                    {option.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </FormControl>
            {description ? (
              <FormDescription>{description}</FormDescription>
            ) : null}
            <FormMessage />
          </FormItem>
        );
      }}
    />
  );
}
