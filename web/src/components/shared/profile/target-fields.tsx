/**
 * What the person aims for (opportunities O4), in the Search tab: the seniority levels and the pay
 * sought, then, under the shared Advanced, the industries and the company types. Seniority,
 * industries and company types add a small bonus to the fit of a posting that names them (at most
 * 10 points, api/app/jobs/scoring.py `bonus`); they never hide a posting. The pay changes no score.
 * Industries are words only, with no regular expression switch (owner, 2026-10-28).
 */
import { useState } from "react";
import { type UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { FieldTitle } from "@/components/ui/field";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import {
  COMPANY_TYPES,
  CURRENCIES,
  SENIORITIES,
} from "@/features/profile/api/profile-api";
import type { SearchForm } from "@/features/profile/helpers/editor-forms";
import { Group, LabelText, TextField } from "./section-ui";
import { TermChips } from "./term-chips";

const SENIORITY_LABELS: Record<(typeof SENIORITIES)[number], string> = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  lead: "Lead",
  principal: "Principal or staff",
};

const COMPANY_TYPE_LABELS: Record<(typeof COMPANY_TYPES)[number], string> = {
  startup: "Startup",
  scaleup: "Scale-up",
  large_company: "Large company",
  public_sector: "Public sector",
  nonprofit: "Nonprofit",
  agency: "Agency",
  consulting: "Consulting firm",
};

export const BONUS_NOTE =
  "A posting that names one of these scores a little higher. Nothing is hidden because of them.";

/** `bare` leaves out the titled card, for a page that gives the fields its own section. */
export function TargetFields({
  form,
  bare = false,
}: {
  form: UseFormReturn<SearchForm>;
  bare?: boolean;
}) {
  const errors = form.formState.errors;
  const [advanced, setAdvanced] = useState(false);
  const fields = (
    <>
      <Toggles
        form={form}
        name="seniority"
        label="Seniority"
        values={SENIORITIES}
        labels={SENIORITY_LABELS}
      />
      <Pay form={form} which="salary" label="Yearly salary" />
      <Pay form={form} which="daily_rate" label="Daily rate (freelance)" />
      <AdvancedSection
        keepMounted
        open={advanced || errors.industries !== undefined}
        onOpenChange={setAdvanced}
      >
        <TermChips
          form={form}
          name="industries"
          label="Industries"
          description="Sectors such as fintech or gaming, matched as whole words in the posting."
          regexSwitch={false}
        />
        <Toggles
          form={form}
          name="company_types"
          label="Company types"
          values={COMPANY_TYPES}
          labels={COMPANY_TYPE_LABELS}
        />
      </AdvancedSection>
    </>
  );
  if (bare) return fields;
  return (
    <Group title="What you aim for" description={BONUS_NOTE}>
      {fields}
    </Group>
  );
}

function Toggles<V extends string>({
  form,
  name,
  label,
  values,
  labels,
}: {
  form: UseFormReturn<SearchForm>;
  name: "seniority" | "company_types";
  label: string;
  values: readonly V[];
  labels: Record<V, string>;
}) {
  const labelId = `${name}-label`;
  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => {
        const selected = field.value as string[];
        return (
          <FormItem>
            <FieldTitle id={labelId}>{label}</FieldTitle>
            <ToggleGroup
              type="multiple"
              variant="outline"
              spacing={2}
              size="sm"
              aria-labelledby={labelId}
              value={selected}
              onValueChange={(next) => {
                // Kept in the order of the list, whatever the order of the clicks.
                field.onChange(values.filter((value) => next.includes(value)));
              }}
              className="flex-wrap"
            >
              {values.map((value) => (
                <ToggleGroupItem key={value} value={value}>
                  {labels[value]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <FormMessage />
          </FormItem>
        );
      }}
    />
  );
}

function Pay({
  form,
  which,
  label,
}: {
  form: UseFormReturn<SearchForm>;
  which: "salary" | "daily_rate";
  label: string;
}) {
  const base = `compensation.${which}` as const;
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap items-start gap-2">
        <TextField
          control={form.control}
          name={`${base}.min`}
          label="From"
          srSuffix={label}
          inputMode="numeric"
          autoComplete="off"
          className="w-32"
        />
        <TextField
          control={form.control}
          name={`${base}.max`}
          label="To"
          srSuffix={label}
          inputMode="numeric"
          autoComplete="off"
          className="w-32"
        />
        <FormField
          control={form.control}
          name={`${base}.currency`}
          render={({ field }) => (
            <FormItem className="w-28">
              <FormLabel>
                <LabelText label="Currency" srSuffix={label} />
              </FormLabel>
              <FormControl>
                <NativeSelect
                  name={field.name}
                  ref={field.ref}
                  value={field.value}
                  onBlur={field.onBlur}
                  onChange={field.onChange}
                >
                  {CURRENCIES.map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </fieldset>
  );
}
