import { Spinner } from "@/components/ui/spinner";

import {
  PAUSED_ON_CEILING,
  tokens,
  type Run,
} from "@/features/ingestion/api/runs-api";
import {
  DELETED_DOCUMENT,
  runHeadline,
  stateOf,
  stepKind,
  UNANSWERED,
} from "@/features/ingestion/helpers/run-words";

/** The state of the run in a sentence or three, said to a screen reader as it changes. */
export function RunStatus({ run }: { run: Run }) {
  const current =
    run.steps.find((step) => step.status === "running") ??
    run.steps.find((step) => step.status === "planned");
  const position = current ? run.document_ids.indexOf(current.document_id) : -1;
  const reading = run.status === "running" && !run.waiting;
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col gap-1 rounded-lg bg-muted p-3"
      data-testid="run-state"
      data-state={stateOf(run)}
    >
      <p className="flex items-center gap-2 font-medium">
        {run.status === "running" ? (
          <Spinner aria-hidden="true" role={undefined} className="shrink-0" />
        ) : null}
        {runHeadline(run)}
      </p>
      {current && reading ? (
        <p data-testid="run-current">
          {position >= 0
            ? `Document ${position + 1} of ${run.document_ids.length}: `
            : null}
          <span className="break-words">
            {current.document_name ?? DELETED_DOCUMENT}
          </span>
          . {stepKind(current, run.steps)}.
        </p>
      ) : null}
      <RunStatusDetail run={run} />
    </div>
  );
}

/** What the headline needs said under it: why the run waits, what a pause asks. */
function RunStatusDetail({ run }: { run: Run }) {
  const adds = run.tokens.continue_adds;
  const raised = run.tokens.continue_ceiling;
  switch (run.status) {
    case "running":
      if (run.cancel_requested)
        return <p>It ends as soon as the step in progress has ended.</p>;
      return run.waiting !== null ? (
        <p>
          Your other agent runs are using every place you have. This run goes on
          by itself when one of them ends.
        </p>
      ) : null;
    case "paused":
      if (run.paused_reason !== PAUSED_ON_CEILING)
        return (
          <>
            <p data-testid="paused-reason">{run.paused_reason}</p>
            <p>Resume starts again with the step that stopped. {UNANSWERED}</p>
          </>
        );
      return adds !== null && adds > 0 ? (
        <>
          <p data-testid="ceiling-spent">
            It has used {tokens(run.tokens.spent)} tokens, and its limit is{" "}
            {tokens(run.tokens.ceiling)} tokens.
          </p>
          <p data-testid="continue-adds">
            Continue raises the limit by {tokens(adds)} tokens
            {raised !== null ? `, to ${tokens(raised)} tokens,` : null} and the
            run goes on. Cancel ends it here. {UNANSWERED}
          </p>
        </>
      ) : (
        <p>
          It has used {tokens(run.tokens.spent)} tokens. Nothing is left to
          read, so the run is closing by itself.
        </p>
      );
    case "done":
      return null;
    case "failed":
      return run.closed_reason ? <p>{run.closed_reason}</p> : null;
    default:
      return <p>Nothing it read was added to your profile.</p>;
  }
}
