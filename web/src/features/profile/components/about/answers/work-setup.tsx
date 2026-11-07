import { useWatch, type Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { FoldedGroup } from "../folded-group";
import { ChoiceToggles } from "./choice-toggles";
import { filled, GROUP_FIELDS, YES_NO_NOT_SAID } from "./choices";

/** The yes or no answers forms ask for: on site, hybrid, applied before, and the consents. */
export function WorkSetup({ control }: { control: Control<AnswersForm> }) {
  const values = useWatch({ control, name: GROUP_FIELDS.setup });
  return (
    <FoldedGroup
      value="setup"
      title="Work setup and consents"
      summary={`${filled(values)} of ${values.length} said`}
      description="Your yes or no for questions forms ask. Not said leaves the field to you. Consents are never assumed."
    >
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <ChoiceToggles
          control={control}
          name="willing_onsite"
          label="Willing to work on site"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceToggles
          control={control}
          name="willing_hybrid"
          label="Willing to work hybrid"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceToggles
          control={control}
          name="previously_applied_default"
          label="Previously applied or worked at a company"
          description="The default answer. Your own applications in this app are checked first."
          options={YES_NO_NOT_SAID}
        />
        <ChoiceToggles
          control={control}
          name="background_check_consent"
          label="Consent to a background check"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceToggles
          control={control}
          name="reference_check_consent"
          label="Consent to a reference check"
          options={YES_NO_NOT_SAID}
        />
      </div>
    </FoldedGroup>
  );
}
