import { ModelAccess } from "@/components/shared/agents/model-access";
import { SettingsSection } from "@/components/shared/profile/settings-frame";

/** Account page, "Model access": the Claude credential the user's agents run on. */
export function ModelAccessCard() {
  return (
    <SettingsSection
      title="Model access"
      description="The Claude credential your agents run on. It is stored encrypted and never shown again: only its last four characters are."
      className="gap-6"
    >
      <ModelAccess />
    </SettingsSection>
  );
}
