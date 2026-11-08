import { useId, useState } from "react";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { WORDING } from "@/lib/wording";

import { readPay, type PanelProps } from "@/features/onboarding/helpers/steps";
import { ONE_BOX } from "./choice-cards";
import { PayFields } from "./pay-fields";

/** Step 8, Pay: "Rather not say", or the yearly salary and the daily rate, folded. */
export function StepPay({ draft, change, problem }: PanelProps) {
  const id = useId();
  const [open, setOpen] = useState<boolean>();
  const error =
    problem && !draft.ratherNotSay
      ? "error" in readPay(draft.salary)
        ? "salary"
        : "error" in readPay(draft.daily)
          ? "daily"
          : null
      : null;
  // A wrong figure opens the fold, during the render that shows the problem.
  if (error && open !== true) setOpen(true);
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Shown on the cards next to a posting&apos;s pay, and written in the
        application forms that ask for it. Rather not say leaves those questions
        blank.
      </p>
      <Label htmlFor={id} className={ONE_BOX}>
        <Checkbox
          id={id}
          checked={draft.ratherNotSay}
          onCheckedChange={(checked) => {
            change({ ratherNotSay: checked === true });
          }}
        />
        {WORDING.ratherNotSay}
      </Label>
      {draft.ratherNotSay ? null : (
        <AdvancedSection
          pageKey="welcome-pay"
          open={open}
          onOpenChange={setOpen}
          errorFieldId={error ? `${id}-${error}-min` : undefined}
        >
          <PayFields
            id={`${id}-salary`}
            invalid={error === "salary"}
            legend={WORDING.yearlySalary}
            pay={draft.salary}
            onChange={(salary) => {
              change({ salary });
            }}
          />
          <PayFields
            id={`${id}-daily`}
            invalid={error === "daily"}
            legend={WORDING.dailyRateFreelance}
            pay={draft.daily}
            onChange={(daily) => {
              change({ daily });
            }}
          />
        </AdvancedSection>
      )}
    </div>
  );
}
