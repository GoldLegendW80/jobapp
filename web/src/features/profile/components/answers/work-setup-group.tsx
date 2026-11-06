import type { Control } from "react-hook-form";

import type { AnswersForm } from "@/features/profile/helpers/forms";
import { Group } from "@/components/shared/profile/section-ui";
import { ChoiceField, type Choice } from "./choice-field";

const YES_NO_NOT_SAID: readonly Choice[] = [
  { value: "", label: "Not said" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

/** The yes or no answers forms ask for: on site, hybrid, applied before, and the consents. */
export function WorkSetupGroup({ control }: { control: Control<AnswersForm> }) {
  return (
    <Group
      title="Work setup and consents"
      description="Your yes or no for questions forms ask. Not said means the field is left to you. Consents are never assumed."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <ChoiceField
          control={control}
          name="willing_onsite"
          label="Willing to work on site"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceField
          control={control}
          name="willing_hybrid"
          label="Willing to work hybrid"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceField
          control={control}
          name="previously_applied_default"
          label="Previously applied or worked at a company"
          description="The default answer. Your own applications in this app are checked first."
          options={YES_NO_NOT_SAID}
        />
        <ChoiceField
          control={control}
          name="background_check_consent"
          label="Consent to a background check"
          options={YES_NO_NOT_SAID}
        />
        <ChoiceField
          control={control}
          name="reference_check_consent"
          label="Consent to a reference check"
          options={YES_NO_NOT_SAID}
        />
      </div>
    </Group>
  );
}
