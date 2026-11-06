import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import type { Profile } from "@/features/profile/api/profile-api";
import { ConflictDialog } from "@/components/shared/profile/section-ui";
import { WORDING } from "@/lib/wording";

import type { OnboardingState } from "../../api/onboarding-api";
import { useWizard } from "@/features/onboarding/api/use-wizard";
import { WizardNav } from "./wizard-nav";
import { WizardProgress } from "./wizard-progress";
import { WizardStep } from "./wizard-step";

/** The wizard once the profile and the steps passed are loaded: header, progress, the open
 * step, the bar under it, and the profile's conflict dialog. */
export function Wizard({
  profile,
  state,
  settings,
}: {
  profile: Profile;
  state: OnboardingState;
  settings: ApplySettings | undefined;
}) {
  const wizard = useWizard({ profile, state, settings });
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="flex flex-col gap-4">
        <PageHeader
          // The skip stays beside the title from sm up, however long the sentence under it.
          className="sm:flex-nowrap sm:items-start"
          title={WORDING.welcome}
          description="A few short answers, and the app looks for postings that fit. You can change every answer later in your profile."
          actions={
            state.completed_at ? null : (
              <Button
                variant="ghost"
                disabled={wizard.busy}
                onClick={wizard.putOff}
              >
                {WORDING.skipForNow}
              </Button>
            )
          }
        />
        <WizardProgress
          current={wizard.index}
          onOpen={(open) => {
            wizard.goTo(open);
          }}
          reachable={wizard.passed}
        />
      </div>

      <WizardStep wizard={wizard} settings={settings} />

      <WizardNav wizard={wizard} />

      <ConflictDialog
        open={wizard.conflict !== undefined}
        label={WORDING.yourProfile}
        onKeepEditing={wizard.keepEditing}
        onReload={wizard.reload}
      />
    </div>
  );
}
