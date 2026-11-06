import { TriangleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import type { Search } from "@/features/profile/api/profile-api";
import { WORDING } from "@/lib/wording";

import {
  STEP_IDS,
  STEP_NAMES,
  type Draft,
  type StepId,
} from "@/features/onboarding/helpers/steps";
import {
  summaryRows,
  sweepCoverage,
} from "@/features/onboarding/helpers/words";
import { ModelKeyForm } from "../model-key-form";

/** Step 8, the summary: each step's saved answer with Change, where the first search looks,
 * and the model key. */
export function StepSummary({
  draft,
  settings,
  search,
  onChange,
}: {
  draft: Draft;
  settings: ApplySettings | undefined;
  search: Search;
  onChange: (step: StepId) => void;
}) {
  const rows = summaryRows(draft, settings, search);
  const places = sweepCoverage(search);
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col gap-3">
        {STEP_IDS.filter((step) => step !== "summary").map((step) => (
          <div
            key={step}
            className="flex items-start justify-between gap-3 rounded-md border px-3 py-2"
            data-simple-card
          >
            <div className="flex min-w-0 flex-col">
              <dt className="text-sm font-medium">{STEP_NAMES[step]}</dt>
              <dd className="text-sm break-words text-muted-foreground">
                {rows[step]}
              </dd>
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
      <ModelKeyForm />
    </div>
  );
}
