import { useWatch, type Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { AreaField } from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";
import { filled, GROUP_FIELDS } from "./choices";

/** The motivation letters and answers start from, and how the person heard about the role. */
export function OwnWords({ control }: { control: Control<AnswersForm> }) {
  const values = useWatch({ control, name: GROUP_FIELDS["own-words"] });
  return (
    <FoldedGroup
      value="own-words"
      title="In your own words"
      summary={`${filled(values)} of ${values.length} answered`}
    >
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
    </FoldedGroup>
  );
}
