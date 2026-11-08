import { Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import {
  STEP_IDS,
  STEP_NAMES,
  type StepId,
} from "@/features/onboarding/helpers/steps";

/** One box for every name of the list, a button or not, so they all sit on one line. */
const STEP_LABEL =
  "inline-flex min-h-8 w-full items-center gap-2 rounded-md px-2 leading-5 whitespace-nowrap";

/** The bar above the step, at every width: its value is the step, 1 to 10. */
export function WizardBar({ current }: { current: number }) {
  const name = STEP_NAMES[STEP_IDS[current] ?? "profile"];
  return (
    <Progress
      value={((current + 1) / STEP_IDS.length) * 100}
      aria-label={WORDING.wizardProgress}
      aria-valuemin={1}
      aria-valuemax={STEP_IDS.length}
      aria-valuenow={current + 1}
      aria-valuetext={`Step ${current + 1} of ${STEP_IDS.length}: ${name}`}
      className="h-1.5 bg-muted *:data-[slot=progress-indicator]:motion-reduce:transition-none"
    />
  );
}

/**
 * The ten names in the side rail, once the page is wide enough for one: the current one
 * marked, a step already passed opens. Narrower, only the bar shows: the heading under it says
 * "Step N of 10: Name", and Back and the summary's Change open the steps passed.
 */
export function WizardSteps({
  current,
  reachable,
  onOpen,
}: {
  current: number;
  reachable: readonly StepId[];
  onOpen: (step: StepId) => void;
}) {
  return (
    <Card className="hidden py-4 @4xl:flex">
      <CardContent className="px-4">
        <nav aria-label={WORDING.progress}>
          <ol
            className="hidden flex-col gap-0.5 text-sm @4xl:flex"
            data-testid="welcome-step-names"
          >
            {STEP_IDS.map((step, at) => {
              const open = at === current;
              const passed = at < current || reachable.includes(step);
              const canOpen = !open && passed;
              const mark = (
                <span
                  aria-hidden="true"
                  className={cn(
                    "grid size-5 shrink-0 place-items-center rounded-full border text-[0.625rem] tabular-nums",
                    open && "border-primary bg-primary text-primary-foreground",
                    !open && passed && "border-success/30 bg-success/10",
                  )}
                >
                  {!open && passed ? (
                    <Check className="size-3 text-success" />
                  ) : (
                    at + 1
                  )}
                </span>
              );
              return (
                <li key={step} className="flex">
                  {canOpen ? (
                    <Button
                      type="button"
                      variant="ghost"
                      className={cn(
                        "h-auto justify-start font-normal text-muted-foreground",
                        STEP_LABEL,
                      )}
                      onClick={() => {
                        onOpen(step);
                      }}
                    >
                      {mark}
                      {STEP_NAMES[step]}
                    </Button>
                  ) : (
                    <span
                      aria-current={open ? "step" : undefined}
                      className={cn(
                        STEP_LABEL,
                        open
                          ? "bg-muted font-medium text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {mark}
                      {STEP_NAMES[step]}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
      </CardContent>
    </Card>
  );
}
