import { RotateCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

import { loadFailureSentence } from "@/features/ingestion/api/documents-api";
import { isActive, useShownRun } from "@/features/ingestion/api/runs-api";
import { RunView } from "./run-progress/run-view";
import { RUN_HEADING_ID } from "./run-start";

/**
 * Step 3, second part: the run while it works (which document, what was found, the tokens, the
 * pauses and the one action that leaves each of them) and its summary once it has ended.
 */
export function RunProgress() {
  const { id, run } = useShownRun();
  if (id === undefined) return null;
  return (
    <Card
      role="region"
      aria-labelledby={RUN_HEADING_ID}
      className="gap-4 p-4 sm:p-6"
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
        <div className="flex flex-col items-start gap-3">
          <Alert variant="destructive">
            <AlertTitle>The run could not be loaded</AlertTitle>
            <AlertDescription>
              {loadFailureSentence(run.error)}
            </AlertDescription>
          </Alert>
          <Button
            variant="outline"
            disabled={run.isFetching}
            onClick={() => void run.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
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
