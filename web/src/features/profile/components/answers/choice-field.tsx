import type { Control } from "react-hook-form";

import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { LabelText } from "@/components/shared/profile/section-ui";

export interface Choice {
  value: string;
  label: string;
}

/** A native select over a short list of values, with its label, description and error. */
export function ChoiceField({
  control,
  name,
  label,
  srSuffix,
  description,
  options,
  className,
}: {
  control: Control<AnswersForm>;
  name:
    | "eeo_policy"
    | "salary_stance"
    | `eeo_answers.${number}.topic`
    | `salary_ranges.${number}.period`
    | "willing_onsite"
    | "willing_hybrid"
    | "previously_applied_default"
    | "background_check_consent"
    | "reference_check_consent";
  label: string;
  srSuffix?: string;
  description?: string;
  options: readonly Choice[];
  className?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>
            <LabelText label={label} srSuffix={srSuffix} />
          </FormLabel>
          <FormControl>
            <NativeSelect {...field}>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
          </FormControl>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
