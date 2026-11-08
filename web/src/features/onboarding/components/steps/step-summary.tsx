import { TriangleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import type { Answers, Search } from "@/features/profile/api/profile-api";
import { WORDING } from "@/lib/wording";

import {
  STEP_IDS,
  STEP_NAMES,
  type Draft,
  type StepId,
} from "@/features/onboarding/helpers/steps";
import { useModelKey } from "@/features/agents/api/use-model-key";
import {
  answersLeft,
  summaryRows,
  sweepCoverage,
} from "@/features/onboarding/helpers/words";

/** Step 10, the summary: each step's saved answer with Change, and where the first search
 * looks. */
export function StepSummary({
  draft,
  settings,
  search,
  answers,
  onChange,
}: {
  draft: Draft;
  settings: ApplySettings | undefined;
  search: Search;
  answers: Answers;
  onChange: (step: StepId) => void;
}) {
  const rows = summaryRows(draft, settings, search, useModelKey().data);
  const places = sweepCoverage(search);
  const left = answersLeft(draft, answers);
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col divide-y">
        {STEP_IDS.filter(
          (step): step is Exclude<StepId, "summary" | "building"> =>
            step !== "summary" && step !== "building",
        ).map((step) => (
          <div
            key={step}
            className="flex items-start justify-between gap-3 py-3 first:pt-0"
            data-simple-card
          >
            <div className="flex min-w-0 flex-col">
              <dt className="text-sm font-medium">{STEP_NAMES[step]}</dt>
              {rows[step].map((line) => (
                <dd
                  key={line}
                  className="line-clamp-3 text-sm break-words text-muted-foreground"
                >
                  {line}
                </dd>
              ))}
            </div>
            <Button
              variant="outline"
              size="sm"
              aria-label={`${WORDING.change} ${STEP_NAMES[step]}`}
              onClick={() => {
                onChange(step);
              }}
            >
              {WORDING.change}
            </Button>
          </div>
        ))}
      </dl>
      {left.length ? (
        <p className="text-sm text-muted-foreground">
          Still to give for a complete profile: {left.join(", ")}. Application
          forms often ask for them. Give them with Change, or later in your
          profile.
        </p>
      ) : null}
      {places ? (
        <p className="text-sm text-muted-foreground">
          Start runs your first search on{" "}
          {places === 1 ? "1 place" : `${String(places)} places`} from your
          list.
        </p>
      ) : (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertDescription>
            <p>
              Your list of websites is empty, so the first search has nowhere to
              look and finds no postings.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => {
                onChange("sites");
              }}
            >
              {WORDING.addWebsites}
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
