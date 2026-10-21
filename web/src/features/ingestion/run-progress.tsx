import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { RunPanel } from "@/features/agents/run-panel";

import {
  documentsKey,
  listDocuments,
  loadFailureSentence,
} from "./documents-api";
import { RUN_HEADING_ID } from "./run-start";
import {
  actionFailure,
  cancelRun,
  continueRefusal,
  continueRun,
  isActive,
  PAUSED_ON_CEILING,
  resumeRun,
  runKey,
  refreshRuns,
  tokens,
  useShownRun,
  type Run,
  type RunStep,
} from "./runs-api";

/**
 * Step 3, second part: the run while it works (which document, what was found, the tokens, the
 * pauses and the one action that leaves each of them) and its summary once it has ended.
 */
export function RunProgress() {
  const { id, run } = useShownRun();
  if (id === undefined) return null;
  return (
    <section aria-labelledby={RUN_HEADING_ID} className="flex flex-col gap-4">
      <h3
        id={RUN_HEADING_ID}
        tabIndex={-1}
        className="text-base font-semibold tracking-tight outline-none"
      >
        {run.data && !isActive(run.data.status)
          ? "Your last run"
          : "The run in progress"}
      </h3>
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
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the run
        </p>
      )}
    </section>
  );
}

const plural = (value: number, one: string, many: string) =>
  `${tokens(value)} ${value === 1 ? one : many}`;

/** What a step does, in words. */
function stepKind(step: RunStep, steps: readonly RunStep[]): string {
  if (step.kind === "transcribe") return "Reading its page images";
  const parts = steps.filter(
    (one) => one.kind === step.kind && one.document_id === step.document_id,
  );
  return parts.length > 1
    ? `Reading its text, part ${parts.indexOf(step) + 1} of ${parts.length}`
    : "Reading its text";
}

const STEP_WORDS: Record<string, string> = {
  planned: "Waiting for its turn",
  running: "Being read",
  done: "Read",
  reused: "Already read in an earlier run, reused",
  failed: "Not read",
  cancelled: "Left out",
};

const DELETED = "Deleted document";
/** What a pause left alone becomes (the engine's PAUSE_MAX). */
const UNANSWERED =
  "Left paused for 24 hours, the run ends with what it has read.";

function RunView({ run }: { run: Run }) {
  const client = useQueryClient();
  const [failure, setFailure] = useState<string>();
  /** Set when a Continue was refused because its figure had changed: nothing was added. */
  const [changed, setChanged] = useState<string>();
  const [watching, setWatching] = useState(false);

  const settle = async (updated: Run) => {
    // A read sent before the click would land after this answer and bring the old state back.
    await client.cancelQueries({ queryKey: runKey(updated.id) });
    client.setQueryData(runKey(updated.id), updated);
    refreshRuns(client);
  };
  const action = useMutation({
    mutationFn: (what: {
      name: "resume" | "cancel" | "continue";
      adds?: number;
    }) =>
      what.name === "continue"
        ? continueRun(run.id, what.adds ?? 0)
        : what.name === "resume"
          ? resumeRun(run.id)
          : cancelRun(run.id),
    onMutate: () => {
      setFailure(undefined);
      setChanged(undefined);
    },
    onSuccess: settle,
    onError: (error, what) => {
      const refusal =
        what.name === "continue" ? continueRefusal(error) : undefined;
      if (refusal) {
        // Never a success: the ceiling did not move. The figure of now is shown instead.
        if (refusal.run) void settle(refusal.run);
        else void client.invalidateQueries({ queryKey: runKey(run.id) });
        setChanged(
          refusal.continueAdds > 0
            ? `Nothing was added: the figure has changed. Continue now adds ${tokens(refusal.continueAdds)} tokens. Click it again if you agree.`
            : "Nothing was added: nothing is left to read, so the run is closing.",
        );
        return;
      }
      setFailure(
        actionFailure(error, "That did not go through. Try again in a moment."),
      );
      void client.invalidateQueries({ queryKey: runKey(run.id) });
    },
  });

  const documents = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
  });
  const nameOf = (documentId: string): string => {
    const step = run.steps.find((one) => one.document_id === documentId);
    if (step) return step.document_name ?? DELETED;
    return (
      documents.data?.items.find((one) => one.id === documentId)?.name ??
      DELETED
    );
  };

  const current =
    run.steps.find((step) => step.status === "running") ??
    run.steps.find((step) => step.status === "planned");
  const onCeiling = run.paused_reason === PAUSED_ON_CEILING;
  const adds = run.tokens.continue_adds;
  const raised = run.tokens.continue_ceiling;
  const busy = action.isPending;
  const cancel = (
    <Button
      variant="outline"
      disabled={busy || run.cancel_requested}
      onClick={() => {
        action.mutate({ name: "cancel" });
      }}
    >
      Cancel the run
    </Button>
  );

  let headline: string;
  let body: ReactNode = null;
  let buttons: ReactNode = null;
  if (run.status === "running") {
    buttons = cancel;
    if (run.cancel_requested) {
      headline = "Stopping the run.";
      body = <p>It ends as soon as the step in progress has ended.</p>;
    } else if (run.waiting !== null) {
      headline = "The run is waiting for its turn.";
      body = (
        <p>
          Your other agent runs are using every place you have. This run goes on
          by itself when one of them ends.
        </p>
      );
    } else {
      headline = "The agent is reading your documents.";
    }
  } else if (run.status === "paused" && onCeiling) {
    headline = "The run has paused at its token ceiling.";
    if (adds !== null && adds > 0) {
      body = (
        <>
          <p data-testid="ceiling-spent">
            It has used {tokens(run.tokens.spent)} tokens, and its ceiling is{" "}
            {tokens(run.tokens.ceiling)} tokens.
          </p>
          <p data-testid="continue-adds">
            Continue raises the ceiling by {tokens(adds)} tokens
            {raised !== null ? `, to ${tokens(raised)} tokens,` : null} and the
            run goes on. Cancel ends it here. {UNANSWERED}
          </p>
        </>
      );
      buttons = (
        <>
          <Button
            disabled={busy}
            onClick={() => {
              // The figure on screen is the figure agreed to, and the one sent.
              action.mutate({ name: "continue", adds });
            }}
          >
            Continue with {tokens(adds)} more tokens
          </Button>
          {cancel}
        </>
      );
    } else {
      body = (
        <p>
          It has used {tokens(run.tokens.spent)} tokens. Nothing is left to
          read, so the run is closing by itself.
        </p>
      );
    }
  } else if (run.status === "paused") {
    headline = "The run is paused.";
    body = (
      <>
        <p data-testid="paused-reason">{run.paused_reason}</p>
        <p>Resume starts again with the step that stopped. {UNANSWERED}</p>
      </>
    );
    buttons = (
      <>
        <Button
          disabled={busy}
          onClick={() => {
            action.mutate({ name: "resume" });
          }}
        >
          Resume the run
        </Button>
        {cancel}
      </>
    );
  } else if (run.status === "done") {
    headline = run.partial
      ? "The run is finished, but not everything was read."
      : "The run is finished. Everything you selected was read.";
  } else if (run.status === "failed") {
    // A run the engine closed keeps what it had read: its reason says so.
    headline = run.closed_reason
      ? "The run failed."
      : "The run failed. Nothing was read.";
    body = run.closed_reason ? <p>{run.closed_reason}</p> : null;
  } else {
    headline = "The run was cancelled.";
    body = <p>Nothing it read was added to your profile.</p>;
  }

  const active = isActive(run.status);
  const position = current ? run.document_ids.indexOf(current.document_id) : -1;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div
        role="status"
        aria-live="polite"
        className="flex flex-col gap-1 rounded-lg border p-3"
        data-testid="run-state"
        data-state={stateOf(run)}
      >
        <p className="flex items-center gap-2 font-medium">
          {run.status === "running" ? (
            <LoaderCircle
              aria-hidden="true"
              className="size-4 shrink-0 animate-spin"
            />
          ) : null}
          {headline}
        </p>
        {active && current && run.status === "running" && !run.waiting ? (
          <p data-testid="run-current">
            {position >= 0
              ? `Document ${position + 1} of ${run.document_ids.length}: `
              : null}
            <span className="break-words">
              {current.document_name ?? DELETED}
            </span>
            . {stepKind(current, run.steps)}.
          </p>
        ) : null}
        {body}
      </div>

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
      {buttons ? <div className="flex flex-wrap gap-2">{buttons}</div> : null}

      <dl
        className="grid gap-x-6 gap-y-2 sm:grid-cols-2"
        data-testid="run-figures"
      >
        {active ? (
          <Figure label="Steps">
            {tokens(run.counts.done)} of {tokens(run.counts.steps)} read
            {run.counts.reused > 0
              ? `, ${tokens(run.counts.reused)} reused from an earlier run`
              : null}
            {run.counts.failed > 0
              ? `, ${tokens(run.counts.failed)} not read`
              : null}
          </Figure>
        ) : (
          <Figure label="Documents">
            {tokens(run.document_ids.length - run.unread_document_ids.length)}{" "}
            of {tokens(run.document_ids.length)} read
            {reusedDocuments(run) > 0
              ? `, ${tokens(reusedDocuments(run))} reused from an earlier run`
              : null}
          </Figure>
        )}
        <Figure label="Found so far">
          {plural(run.counts.items, "item", "items")}
          {run.counts.refused > 0
            ? `, ${tokens(run.counts.refused)} refused by the checks`
            : null}
        </Figure>
        <Figure label="Tokens">
          {tokens(run.tokens.spent)} used
          {run.status === "running"
            ? `. The run pauses at ${tokens(run.tokens.ceiling)} tokens`
            : null}
        </Figure>
        {run.cost_usd !== null ? (
          <Figure label={active ? "Cost so far" : "Cost"}>
            about {run.cost_usd} USD,{" "}
            {active
              ? "each attempt counted when it ends"
              : "every attempt included"}
          </Figure>
        ) : null}
        {!active && run.status === "done" ? (
          <Figure label="Proposals">
            {plural(run.counts.proposals, "proposal", "proposals")} to review
          </Figure>
        ) : null}
      </dl>
      {active ? (
        <p className="text-xs text-muted-foreground">
          {run.tokens.overshoot} While a step is being read the figure is
          behind: what the step writes is counted when it ends. If the server
          restarts during a step, the request it was making is not counted.
        </p>
      ) : null}
      {!active && run.counts.two_digit_year > 0 ? (
        <p>
          Dates written with two digit years were not read (
          {tokens(run.counts.two_digit_year)}).
        </p>
      ) : null}

      {!active && run.unread_document_ids.length > 0 ? (
        <section aria-label="Documents that were not read">
          <h4 className="font-semibold">Not read</h4>
          <ul className="mt-1 flex flex-col gap-1">
            {run.unread_document_ids.map((documentId) => (
              <li key={documentId} className="break-words">
                <span className="font-medium">{nameOf(documentId)}</span>:{" "}
                {whyUnread(run, documentId)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer font-medium">
          Every step of this run
        </summary>
        <ol className="mt-2 flex flex-col gap-2">
          {run.steps.map((step) => (
            <li key={step.id} className="min-w-0 break-words">
              <span className="font-medium">
                {step.document_name ?? DELETED}
              </span>
              . {stepKind(step, run.steps)}.{" "}
              {STEP_WORDS[step.status] ?? "Not read"}
              {step.status === "done" || step.status === "running"
                ? `, ${plural(step.items, "item", "items")}`
                : null}
              {step.refused > 0 ? `, ${tokens(step.refused)} refused` : null}
              {step.tokens !== null
                ? `, ${tokens(step.tokens)} tokens in its last attempt`
                : null}
              {step.cost_usd !== null
                ? `, about ${step.cost_usd} USD for that attempt`
                : null}
              .{step.error ? ` ${step.error}` : null}
            </li>
          ))}
        </ol>
      </details>

      {run.current_agent_run_id !== null ? (
        <details
          className="rounded-lg border p-3"
          onToggle={(event) => {
            setWatching(event.currentTarget.open);
          }}
        >
          <summary className="cursor-pointer font-medium">
            What the agent is doing now
          </summary>
          {watching ? (
            <div className="mt-2 min-w-0">
              {/* The run's own Cancel stops the whole run: the panel offers none. */}
              <RunPanel runId={run.current_agent_run_id} cancellable={false} />
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}

/** A word for tests and styles, never shown. */
function stateOf(run: Run): string {
  if (run.status === "paused")
    return run.paused_reason === PAUSED_ON_CEILING ? "ceiling" : "paused";
  if (run.status === "done" && run.partial) return "partial";
  return run.status;
}

/** The selected documents whose every step was reused from an earlier run. */
function reusedDocuments(run: Run): number {
  return run.document_ids.filter((documentId) => {
    const steps = run.steps.filter((step) => step.document_id === documentId);
    return steps.length > 0 && steps.every((step) => step.status === "reused");
  }).length;
}

/** Why a selected document has nothing read, in a sentence. */
function whyUnread(run: Run, documentId: string): string {
  const steps = run.steps.filter((step) => step.document_id === documentId);
  if (steps.some((step) => step.document_name === null))
    return "it was deleted before it was read.";
  const failed = steps.find((step) => step.error);
  if (failed?.error) return failed.error;
  if (run.status === "cancelled") return "the run was cancelled before it.";
  if (steps.length === 0) return "it did not fit in this run.";
  return "the run ended before it was read.";
}

function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </div>
  );
}
