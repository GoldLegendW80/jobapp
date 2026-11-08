import { Sparkles } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";

import { RunProgress } from "@/components/shared/ingestion/run-progress";
import { ConsentBox } from "@/components/shared/ingestion/run-start/consent-box";
import { EstimateBlockers } from "@/components/shared/ingestion/run-start/estimate-blockers";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import { tokens } from "@/features/ingestion/api/runs-api";
import { useBuilding } from "@/features/onboarding/api/use-building";
import { waitingWords } from "@/features/onboarding/helpers/words";
import type { Profile } from "@/features/profile/api/profile-api";

import { BuildingAnimation } from "../building/building-animation";

/** What the step says under the animation, for each phase of the building. */
function sentence(building: ReturnType<typeof useBuilding>): string {
  const { phase, run, outcome } = building;
  if (phase === "writing") return "Writing what was found into your profile";
  if (phase === "ready") {
    const facts = outcome?.written ?? 0;
    return facts === 0
      ? "Your profile is ready: nothing new was found to add."
      : `Your profile is ready: ${facts} ${facts === 1 ? "fact" : "facts"} added.`;
  }
  if (run?.status === "paused") return "The reading paused";
  if (!run) return "Starting the reading";
  const ended = run.counts.done + run.counts.reused + run.counts.failed;
  return `Reading your files: ${ended} of ${run.counts.steps} parts`;
}

/**
 * Step 3, Building your profile: the reading of every file of step 2 into the profile, the Rive
 * animation while it runs, then what adds to the profile written at once (use-building.ts).
 * `onHold` keeps Next waiting while it reads and writes; "Skip this step" still goes on, the
 * reading going on without the page, its proposals then waiting in Fill my profile.
 */
export function StepBuilding({
  onWritten,
  onHold,
}: {
  onWritten: (profile: Profile) => void;
  onHold: (hold: boolean) => void;
}) {
  const building = useBuilding(onWritten);
  const { phase, count, reading } = building;
  const {
    estimate,
    consent,
    notice,
    noticeRef,
    failure,
    ticked,
    changed,
    starting,
    tick,
    ready,
    agreed,
    begin,
  } = reading;

  const waiting = phase === "reading" || phase === "writing";
  useEffect(() => {
    onHold(waiting);
    return () => {
      onHold(false);
    };
  }, [onHold, waiting]);

  if (phase === "idle") {
    if (building.loading)
      return (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner aria-hidden="true" role={undefined} />
          Loading your files
        </p>
      );
    if (count === 0)
      return (
        <p className="max-w-prose text-sm text-muted-foreground">
          No file to read yet. Go back to Your profile to add your CV, or go on:
          you can fill your profile later.
        </p>
      );
    const files = count === 1 ? "your file" : `your ${count} files`;
    return (
      <div className="flex flex-col gap-4">
        <p className="max-w-prose text-sm text-muted-foreground">
          The app reads {files} and builds your profile from them: your
          experience, skills, projects and what you are looking for.
          {estimate
            ? ` About ${tokens(estimate.tokens.low)} to ${tokens(estimate.tokens.high)} tokens.`
            : null}{" "}
          What would change a value you already have waits for you in Fill my
          profile.
        </p>
        {estimate && !estimate.can_start ? (
          <EstimateBlockers estimate={estimate} />
        ) : null}
        {consent.data && !consent.data.accepted ? (
          <ConsentBox
            consent={consent.data}
            ticked={ticked}
            changed={changed}
            disabled={starting}
            onTick={tick}
          />
        ) : null}
        <div
          ref={noticeRef}
          tabIndex={-1}
          className="outline-none empty:hidden"
        >
          {notice ? (
            <p role="alert" className="text-sm font-medium text-destructive">
              {notice}
            </p>
          ) : null}
        </div>
        {failure ? (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        ) : null}
        <Button
          size="lg"
          className="w-fit"
          disabled={!ready || !agreed || starting}
          aria-busy={starting}
          onClick={begin}
        >
          {starting ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <Sparkles aria-hidden="true" />
          )}
          Build my profile
        </Button>
      </div>
    );
  }

  const run = building.run;
  const outcome = building.outcome;
  const left =
    outcome && outcome.waiting > 0
      ? waitingWords(outcome.waiting, outcome.changing)
      : null;
  return (
    <div className="flex flex-col gap-4">
      <BuildingAnimation
        progress={building.progress}
        finished={phase === "ready"}
        className="h-64 w-full sm:h-80"
      />
      <div className="mx-auto flex w-full max-w-md flex-col gap-2">
        <Progress
          value={building.progress}
          aria-label="How far the reading is"
        />
        <p
          role="status"
          aria-live="polite"
          className="text-center text-sm text-muted-foreground"
          data-testid="building-status"
        >
          {sentence(building)}
        </p>
        {phase === "ready" && left ? (
          <p className="text-center text-sm text-muted-foreground">
            {left.lead} in{" "}
            <Link
              to="/profile/fill?step=review"
              className="underline underline-offset-4"
            >
              Fill my profile
            </Link>
            : {left.reason}.
          </p>
        ) : null}
        {phase === "reading" ? (
          <p className="text-center text-xs text-muted-foreground">
            It can take a few minutes. Skip this step to go on meanwhile: what
            it finds then waits for you in Fill my profile.
          </p>
        ) : null}
      </div>
      {/* A pause, a failure or a cancel: the run's own panel says why and what to do. */}
      {run?.status === "paused" || phase === "stopped" ? (
        <RunProgress flat />
      ) : null}
      {building.writeFailed ? (
        <p role="alert" className="text-sm text-destructive">
          What was found could not be written. Review it in{" "}
          <Link
            to="/profile/fill?step=review"
            className="underline underline-offset-4"
          >
            Fill my profile
          </Link>
          .
        </p>
      ) : null}
    </div>
  );
}
