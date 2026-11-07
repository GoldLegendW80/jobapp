import { SettingsSection } from "@/components/shared/profile/settings-frame";

import { useModelAccess } from "@/features/agents/api/use-model-access";
import { CredentialForm } from "./model-access/credential-form";
import { RemoveCredentialDialog } from "./model-access/remove-credential-dialog";
import { SavedCredential } from "./model-access/saved-credential";
import { VerifyOutcome } from "./model-access/verify-outcome";

/** Account page, "Model access": the Claude credential the user's agents run on. */
export function ModelAccessCard() {
  const access = useModelAccess();
  const { saved, verify, remove } = access;

  return (
    <SettingsSection
      title="Model access"
      description="The Claude credential your agents run on. It is stored encrypted and never shown again: only its last four characters are."
      className="gap-6"
    >
      <SavedCredential
        query={access.query}
        saved={saved}
        verifying={verify.isPending}
        verifyLocked={access.verifyWait.remaining > 0}
        verifyWait={access.verifyWait.remaining}
        onVerify={() => {
          verify.mutate();
        }}
        onRemove={access.askToRemove}
      />
      {access.outcome ? <VerifyOutcome outcome={access.outcome} /> : null}

      <CredentialForm
        saved={saved}
        onSaving={access.clearOutcome}
        onSaved={access.setSaved}
      />

      <RemoveCredentialDialog
        open={access.confirming}
        removing={remove.isPending}
        error={remove.error}
        onClose={access.closeRemove}
        onRemove={() => {
          remove.mutate();
        }}
      />
    </SettingsSection>
  );
}
