import { Card, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";

import { isActive, useShownRun } from "@/features/ingestion/api/runs-api";
import { LoadError } from "@/components/blocks/load-error";
import { loadFailureSentence } from "@/features/ingestion/api/documents-api";
import { RunView } from "./run-progress/run-view";
import { FLAT, RUN_HEADING_ID } from "./run-start";

/**
 * Step 3, second part: the run while it works (which document, what was found, the tokens, the
 * pauses and the one action that leaves each of them) and its summary once it has ended.
 * `flat`: inside a card already, no surface of its own.
 */
export function RunProgress({ flat = false }: { flat?: boolean } = {}) {
  const { id, run } = useShownRun();
  if (id === undefined) return null;
  return (
    <Card
      role="region"
      aria-labelledby={RUN_HEADING_ID}
      className={cn("gap-4", flat ? FLAT : "p-4 sm:p-6")}
    >
      <CardTitle className="text-base tracking-tight">
        <h3 id={RUN_HEADING_ID} tabIndex={-1} className="outline-none">
          {run.data && !isActive(run.data.status)
            ? "Your last run"
            : "The run in progress"}
        </h3>
      </CardTitle>
      {run.data ? (
        <>
          {run.isError ? (
            <p role="status" className="text-sm text-muted-foreground">
              The server could not be reached just now. This page keeps trying.
            </p>
          ) : null}
          {/* A new run is a new view: no message of the run before stays above it. */}
          <RunView key={run.data.id} run={run.data} />
        </>
      ) : run.isError ? (
        <LoadError query={run} reason={loadFailureSentence(run.error)}>
          The run could not be loaded.
        </LoadError>
      ) : (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
          Loading the run
        </p>
      )}
    </Card>
  );
}
