import type { Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { Group, TextField } from "@/components/shared/profile/section-ui";

/** The optional facts about the person some forms ask for: name, pronouns, years, degree. */
export function AboutYouGroup({ control }: { control: Control<AnswersForm> }) {
  return (
    <Group
      title="About you on forms"
      description="Optional. Used when a form asks for these; left empty, the field is left to you."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          control={control}
          name="preferred_name"
          label="Preferred name"
          description="Only for fields that ask what to call you."
        />
        <TextField control={control} name="pronouns" label="Pronouns" />
        <TextField
          control={control}
          name="years_experience_stated"
          label="Years of experience, as you state them"
          placeholder="About 3.5 years"
          className="sm:col-span-2"
        />
        <TextField
          control={control}
          name="highest_degree"
          label="Highest degree"
        />
        <TextField
          control={control}
          name="highest_degree_year"
          label="Year of the highest degree"
          inputMode="numeric"
          placeholder="2022"
        />
      </div>
    </Group>
  );
}
