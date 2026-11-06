import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import {
  STEP_IDS,
  STEP_NAMES,
  type StepId,
} from "@/features/onboarding/helpers/steps";

/** One box for every name of the list, a button or not, so they all sit on one baseline. */
const STEP_LABEL =
  "inline-flex min-h-6 items-center rounded leading-5 whitespace-nowrap";

/**
 * The progress bar and, from sm up, the eight names, the current one marked; a step already
 * passed opens. Below sm only the bar shows: the heading under it says "Step N of 8: Name", the
 * bar's value says it too, and Back and the summary's Change open the steps passed.
 */
export function WizardProgress({
  current,
  reachable,
  onOpen,
}: {
  current: number;
  reachable: readonly StepId[];
  onOpen: (step: StepId) => void;
}) {
  const name = STEP_NAMES[STEP_IDS[current] ?? "profile"];
  return (
    <nav aria-label={WORDING.progress}>
      {/* The value is the share of the bar filled; what is read is the step, 1 to 8. */}
      <Progress
        value={((current + 1) / STEP_IDS.length) * 100}
        aria-label={WORDING.wizardProgress}
        aria-valuemin={1}
        aria-valuemax={STEP_IDS.length}
        aria-valuenow={current + 1}
        aria-valuetext={`Step ${current + 1} of ${STEP_IDS.length}: ${name}`}
        className="h-1.5 bg-muted *:data-[slot=progress-indicator]:motion-reduce:transition-none"
      />
      <ol
        className="mt-2 hidden flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:flex"
        data-testid="welcome-step-names"
      >
        {STEP_IDS.map((step, at) => {
          const open = at === current;
          const canOpen = !open && (at < current || reachable.includes(step));
          return (
            <li key={step} className="flex">
              {canOpen ? (
                <Button
                  type="button"
                  variant="link"
                  // The size first: a text size after it would drop the box's leading-5.
                  className={cn(
                    "h-auto p-0 text-xs font-normal text-muted-foreground",
                    STEP_LABEL,
                  )}
                  onClick={() => {
                    onOpen(step);
                  }}
                >
                  {STEP_NAMES[step]}
                </Button>
              ) : (
                <span
                  aria-current={open ? "step" : undefined}
                  className={cn(
                    STEP_LABEL,
                    open
                      ? "font-semibold text-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {STEP_NAMES[step]}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
