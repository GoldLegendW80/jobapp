import { CircleAlert } from "lucide-react";
import { useEffect, useRef } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import { useCvUpload } from "@/features/documents/api/use-cv-upload";

import type { Wizard } from "@/features/onboarding/api/use-wizard";
import {
  draftOf,
  STEP_IDS,
  STEP_NAMES,
} from "@/features/onboarding/helpers/steps";
import { StepAccess } from "../steps/step-access";
import { StepApply } from "../steps/step-apply";
import { StepBuilding } from "../steps/step-building";
import { StepLooking } from "../steps/step-looking";
import { StepPay } from "../steps/step-pay";
import { StepProfile } from "../steps/step-profile";
import { StepSites } from "../steps/step-sites";
import { StepSummary } from "../steps/step-summary";
import { StepWhere } from "../steps/step-where";
import { StepWork } from "../steps/step-work";

/** The open step in its card: "Step N of 10: Name", what went wrong, and the step's panel. */
export function WizardStep({
  wizard,
  settings,
  onHold,
}: {
  wizard: Wizard;
  settings: ApplySettings | undefined;
  onHold: (hold: boolean) => void;
}) {
  const { step, index, panel, problem, sources, stored } = wizard;
  const heading = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  // Here, not in Your profile: the CV's conversion goes on when the person moves on.
  const cv = useCvUpload({ eager: true });

  useEffect(() => {
    // Each step moved to puts the keyboard on its heading, not the first time.
    if (first.current) first.current = false;
    else heading.current?.focus();
  }, [step]);

  return (
    <Card>
      <CardContent>
        <section
          aria-labelledby="welcome-step"
          className="flex flex-col gap-5"
          data-step={step}
        >
          <h2
            id="welcome-step"
            ref={heading}
            tabIndex={-1}
            className="flex flex-col gap-1 text-lg font-medium tracking-tight outline-none"
          >
            <span className="text-sm font-normal tracking-normal text-muted-foreground">
              Step {index + 1} of {STEP_IDS.length}
              <span className="sr-only">:</span>
            </span>{" "}
            {STEP_NAMES[step]}
          </h2>
          {problem ? (
            <Alert id="welcome-problem" variant="destructive" role="alert">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{problem}</AlertDescription>
            </Alert>
          ) : null}
          {step === "access" ? <StepAccess /> : null}
          {step === "profile" ? <StepProfile cv={cv} /> : null}
          {step === "building" ? (
            <StepBuilding onWritten={wizard.adopt} onHold={onHold} />
          ) : null}
          {step === "looking" ? <StepLooking {...panel} /> : null}
          {step === "where" ? <StepWhere {...panel} /> : null}
          {step === "sites" ? (
            <StepSites sources={sources} reviewed={stored.search} />
          ) : null}
          {step === "work" ? <StepWork {...panel} /> : null}
          {step === "pay" ? <StepPay {...panel} /> : null}
          {step === "apply" ? (
            <StepApply {...panel} settings={settings} />
          ) : null}
          {step === "summary" ? (
            <StepSummary
              draft={draftOf(stored)}
              settings={settings}
              search={stored.search}
              answers={stored.answers}
              onChange={(changed) => {
                wizard.goTo(changed, true);
              }}
            />
          ) : null}
        </section>
      </CardContent>
    </Card>
  );
}
