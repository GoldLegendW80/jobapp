import { useWatch, type Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { AreaField, TextField } from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";
import { filled, GROUP_FIELDS } from "./choices";

/** Notice period, start date, salary policy, relocation and travel, in the person's words. */
export function Availability({ control }: { control: Control<AnswersForm> }) {
  const values = useWatch({ control, name: GROUP_FIELDS.availability });
  return (
    <FoldedGroup
      value="availability"
      title="Availability and conditions"
      summary={`${filled(values)} of ${values.length} answered`}
    >
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <AreaField
          control={control}
          name="notice_period"
          label="Notice period"
          rows={2}
        />
        <TextField
          control={control}
          name="earliest_start"
          label="Earliest start"
          placeholder="December 2026"
        />
        <AreaField
          control={control}
          name="salary_policy"
          label="Salary policy"
          description="What to write when a form asks for an expected salary."
          rows={2}
          className="sm:col-span-2"
        />
        <AreaField
          control={control}
          name="relocation"
          label="Relocation and remote work"
          rows={3}
        />
        <AreaField control={control} name="travel" label="Travel" rows={3} />
      </div>
    </FoldedGroup>
  );
}
