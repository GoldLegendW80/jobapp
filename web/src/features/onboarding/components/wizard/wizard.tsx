import { useState } from "react";

import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import type { Profile } from "@/features/profile/api/profile-api";
import { ConflictDialog } from "@/components/shared/profile/section-ui";
import { WORDING } from "@/lib/wording";

import type { OnboardingState } from "../../api/onboarding-api";
import { useWizard } from "@/features/onboarding/api/use-wizard";
import { WELCOME_LINE } from "@/features/onboarding/helpers/words";
import { WizardNav } from "./wizard-nav";
import { WizardBar, WizardSteps } from "./wizard-progress";
import { WizardStep } from "./wizard-step";

/** The wizard once the profile and the steps passed are loaded: header, the progress bar, the
 * open step, the bar of buttons under it, the ten steps in the side rail, and the profile's
 * conflict dialog. */
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
  // Building your profile keeps Next waiting while it reads and writes.
  const [hold, setHold] = useState(false);
  return (
    <Page>
      <PageHeader
        title={WORDING.welcome}
        description={WELCOME_LINE}
        actions={
          state.completed_at ? null : (
            <Button
              variant="outline"
              disabled={wizard.busy}
              onClick={wizard.putOff}
            >
              {WORDING.skipForNow}
            </Button>
          )
        }
      />
      <PageBody
        aside={
          <WizardSteps
            current={wizard.index}
            onOpen={(open) => {
              wizard.goTo(open);
            }}
            reachable={wizard.passed}
          />
        }
      >
        <div className="flex flex-col gap-6">
          <WizardBar current={wizard.index} />
          <WizardStep wizard={wizard} settings={settings} onHold={setHold} />
          <WizardNav wizard={wizard} hold={hold} />
        </div>
      </PageBody>

      <ConflictDialog
        open={wizard.conflict !== undefined}
        label={WORDING.yourProfile}
        onKeepEditing={wizard.keepEditing}
        onReload={wizard.reload}
      />
    </Page>
  );
}
