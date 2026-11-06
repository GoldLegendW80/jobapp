import type { Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { AreaField, Group } from "@/components/shared/profile/section-ui";

/** The motivation letters and answers start from, and how the person heard about the role. */
export function OwnWordsGroup({ control }: { control: Control<AnswersForm> }) {
  return (
    <Group title="In your own words">
      <AreaField
        control={control}
        name="motivation"
        label="Motivation"
        description="Why you want this kind of role. Letters and answers start from it."
        rows={5}
      />
      <AreaField
        control={control}
        name="heard_about"
        label="How you heard about the role"
        rows={2}
      />
    </Group>
  );
}
