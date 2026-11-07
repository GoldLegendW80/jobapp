import { useId } from "react";

import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { useDebouncedField } from "@/features/jobs/hooks/use-job-filters";
import {
  SEARCH_DEBOUNCE_MS,
  TEXT_MAX,
  type FilterProps,
} from "@/features/jobs/helpers/filters";

/** The company field; `labelled` shows its label above it instead of only to screen readers. */
export function CompanyField({
  filters,
  onChange,
  labelled = false,
}: FilterProps & { labelled?: boolean }) {
  const company = useDebouncedField(
    filters.company,
    (value) => {
      onChange({ ...filters, company: value });
    },
    SEARCH_DEBOUNCE_MS,
  );
  const id = useId();
  const input = (
    <Input
      id={id}
      placeholder={labelled ? "Any company" : "Company"}
      maxLength={TEXT_MAX}
      value={company.draft}
      onChange={(event) => {
        company.setDraft(event.target.value);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") company.flush();
      }}
    />
  );
  return labelled ? (
    <Field className="gap-1.5">
      <FieldLabel htmlFor={id}>Company</FieldLabel>
      {input}
    </Field>
  ) : (
    <div>
      <Label htmlFor={id} className="sr-only">
        Company
      </Label>
      {input}
    </div>
  );
}
