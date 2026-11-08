import { ArrowLeft, ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { WORDING } from "@/lib/wording";

import type { Wizard } from "@/features/onboarding/api/use-wizard";
import { ESSENTIAL, STEP_IDS } from "@/features/onboarding/helpers/steps";

/** The bar under the step, kept in view: Back, "Skip this step" (not on the essential steps),
 * then Next, or Start on the summary. `hold`: a step at work (Building your profile) keeps Next
 * waiting; Skip still goes on. */
export function WizardNav({ wizard, hold }: { wizard: Wizard; hold: boolean }) {
  const { step, index, busy } = wizard;
  return (
    <nav
      aria-label={WORDING.wizard}
      className="sticky bottom-(--bottom-bar,0px) flex flex-wrap items-center justify-between gap-2 bg-background py-3"
    >
      <Button
        variant="outline"
        disabled={index === 0 || busy}
        onClick={() => {
          wizard.goTo(STEP_IDS[index - 1] ?? "profile");
        }}
      >
        <ArrowLeft aria-hidden="true" />
        {WORDING.back}
      </Button>
      <div className="flex flex-wrap gap-2">
        {ESSENTIAL.has(step) ? null : (
          <Button variant="outline" disabled={busy} onClick={wizard.skip}>
            {WORDING.skipThisStep}
          </Button>
        )}
        {step === "summary" ? (
          <Button disabled={busy} onClick={wizard.start}>
            {wizard.starting ? <Spinner aria-hidden="true" /> : null}
            {WORDING.start}
          </Button>
        ) : (
          <Button disabled={busy || hold} onClick={wizard.next}>
            {wizard.saving ? <Spinner aria-hidden="true" /> : null}
            {WORDING.next}
            <ArrowRight aria-hidden="true" />
          </Button>
        )}
      </div>
    </nav>
  );
}
