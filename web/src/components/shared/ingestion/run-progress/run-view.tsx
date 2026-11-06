import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { RunPanel } from "@/components/shared/agents/run-panel";

import { isActive, type Run } from "@/features/ingestion/api/runs-api";
import { useDocuments } from "@/features/ingestion/api/use-documents";
import { useRunActions } from "@/features/ingestion/api/use-run-actions";
import { FoldTrigger } from "./fold-trigger";
import { RunActions } from "./run-actions";
import { RunFigures } from "./run-figures";
import { RunStatus } from "./run-status";
import { RunSteps } from "./run-steps";
import { UnreadDocuments } from "./unread-documents";

/** One run: its state and its action, its figures, what was not read, and every step. */
export function RunView({ run }: { run: Run }) {
  const { act, busy, failure, changed } = useRunActions(run.id);
  const [watching, setWatching] = useState(false);
  const documents = useDocuments();
  const active = isActive(run.status);
  return (
    <div className="flex flex-col gap-4 text-sm">
      <RunStatus run={run} />
      {changed ? (
        <Alert role="alert">
          <AlertTitle>Read this first</AlertTitle>
          <AlertDescription>{changed}</AlertDescription>
        </Alert>
      ) : null}
      {failure ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      ) : null}
      <RunActions run={run} busy={busy} onAction={act} />
      <RunFigures run={run} />
      {!active && run.unread_document_ids.length > 0 ? (
        <UnreadDocuments run={run} documents={documents.data?.items} />
      ) : null}
      <RunSteps run={run} />
      {run.current_agent_run_id !== null ? (
        // The panel connects to the agent's run only while it is open.
        <Collapsible
          className="rounded-lg bg-muted"
          open={watching}
          onOpenChange={setWatching}
        >
          <FoldTrigger>What the agent is doing now</FoldTrigger>
          <CollapsibleContent className="min-w-0 px-3 pb-3">
            {/* The run's own Cancel stops the whole run: the panel offers none. */}
            <RunPanel runId={run.current_agent_run_id} cancellable={false} />
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}
