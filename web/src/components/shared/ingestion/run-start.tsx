import { ChevronRight, Play } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { loadFailureSentence } from "@/features/ingestion/api/documents-api";
import { tokens, useRuns, type Run } from "@/features/ingestion/api/runs-api";
import { useDocuments } from "@/features/ingestion/api/use-documents";
import { useStartRun } from "@/features/ingestion/api/use-start-run";
import { canBeChosen } from "@/features/ingestion/helpers/run-selection";
import { ConsentBox } from "./run-start/consent-box";
import { DocumentChoice } from "./run-start/document-choice";
import { EstimateBlockers } from "./run-start/estimate-blockers";
import { EstimateLine } from "./run-start/estimate-line";
import { EstimateView } from "./run-start/estimate-view";

/** A card already around the part (the welcome wizard): no surface of its own. */
export const FLAT = "rounded-none bg-transparent p-0";

/** The heading of the run on the page (run-progress.tsx): it takes the focus after a start. */
export const RUN_HEADING_ID = "run-heading";

/**
 * Step 3, first part: choosing the documents and starting a run. Nothing is sent to a model
 * from here until the user has read the estimate in the dialog and clicked Start. While a run
 * is active this part steps aside: the page shows that run (run-progress.tsx). `flat`: inside
 * a card already, no surface of its own.
 */
export function RunStart({ flat = false }: { flat?: boolean } = {}) {
  const documents = useDocuments();
  const runs = useRuns();
  /** The documents ticked, in the order they were ticked: the run reads them in that order. */
  const [chosen, setChosen] = useState<readonly string[]>([]);

  if (runs.isPending || documents.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading
      </p>
    );
  // Nothing to show yet: a background read that fails keeps this part and its dialog.
  if (runs.data === undefined || documents.data === undefined)
    return (
      <Alert variant="destructive">
        <AlertTitle>This step could not be loaded</AlertTitle>
        <AlertDescription>
          {loadFailureSentence(runs.error ?? documents.error)}
        </AlertDescription>
      </Alert>
    );
  // One run at a time: the active one is on the page, and it has the buttons.
  if (runs.data.active_run_id !== null) return null;

  const items = documents.data.items;
  const selected = chosen.filter((id) =>
    items.some((one) => one.id === id && canBeChosen(one)),
  );

  return (
    <Card
      role="region"
      aria-labelledby="start-heading"
      className={cn("gap-3", flat ? FLAT : "p-4 sm:p-6")}
    >
      <CardHeader className="gap-1 px-0">
        <CardTitle className="text-base tracking-tight">
          <h3 id="start-heading">
            {runs.data.runs.length > 0
              ? WORDING.startAnotherReading
              : WORDING.startReading}
          </h3>
        </CardTitle>
        <CardDescription>
          Choose the documents the agent reads, in the order you want them read.
          The button says what it would use in tokens: nothing is sent before
          you click it.
        </CardDescription>
      </CardHeader>
      {runs.isError || documents.isError ? (
        <p role="status" className="text-sm text-muted-foreground">
          The server could not be reached just now. This page keeps trying.
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm">
          You have no document yet. Add a file or paste a text first.
        </p>
      ) : (
        <DocumentChoice
          items={items}
          selected={selected}
          onToggle={(id, on) => {
            setChosen((old) => {
              const others = old.filter((one) => one !== id);
              return on ? [...others, id] : others;
            });
          }}
          onChooseAll={setChosen}
        />
      )}
      {selected.length > 0 ? (
        <StartPanel
          documentIds={selected}
          onStarted={() => {
            // The run on the page takes the focus: this part steps aside while it runs.
            window.requestAnimationFrame(() =>
              document.getElementById(RUN_HEADING_ID)?.focus(),
            );
          }}
        />
      ) : items.length > 0 ? (
        <p className="text-sm text-muted-foreground">
          Choose at least one document: the button shows what reading it would
          use.
        </p>
      ) : null}
    </Card>
  );
}

/**
 * The start of a run, in place of the start dialog (owner, 2026-11-05): one button that carries
 * the token range of the estimate exactly as the server gave it, and starts the run with the
 * `quote_id` of that estimate. Under it, the pause and the cost range of an API key; the consent
 * box the first time; and, folded, what is read, the "Read again" choices and the limits. A
 * blocker of the estimate disables the button and says why. A start the server refuses because
 * the figures changed replaces what is shown and waits for another click.
 */
export function StartPanel({
  documentIds,
  label = WORDING.completeProfile,
  onStarted,
}: {
  documentIds: string[];
  /** The words of the button, before the token range. */
  label?: string;
  /** The run was created: the caller follows it. */
  onStarted?: (run: Run) => void;
}) {
  const {
    consent,
    estimate,
    notice,
    noticeRef,
    failure,
    refused,
    reread,
    ticked,
    changed,
    agreed,
    ready,
    estimating,
    starting,
    tick,
    toggleReread,
    begin,
  } = useStartRun({ documentIds, onStarted });
  const text = consent.data;
  const range =
    estimate === undefined
      ? null
      : `about ${tokens(estimate.tokens.low)} to ${tokens(estimate.tokens.high)} tokens`;

  return (
    <section
      aria-label="Before the run starts"
      className="flex flex-col gap-3"
      data-testid="start-panel"
    >
      <div ref={noticeRef} tabIndex={-1} className="outline-none empty:hidden">
        {notice ? (
          <Alert>
            <AlertTitle>Read this first</AlertTitle>
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        ) : null}
      </div>
      {failure ? (
        <Alert variant="destructive">
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      ) : null}
      {estimate !== undefined && estimate.blockers.length > 0 ? (
        <EstimateBlockers estimate={estimate} />
      ) : null}

      {text === undefined ? (
        consent.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {loadFailureSentence(consent.error)}
            </AlertDescription>
          </Alert>
        ) : null
      ) : text.accepted ? null : (
        <ConsentBox
          consent={text}
          ticked={ticked}
          changed={changed}
          disabled={starting}
          onTick={tick}
        />
      )}

      <div className="flex flex-col gap-1">
        <Button
          className="w-full whitespace-normal sm:w-fit"
          disabled={!ready || !agreed || starting}
          onClick={begin}
        >
          {starting || (estimating && estimate === undefined) ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <Play aria-hidden="true" />
          )}
          {range ? `${label} (${range})` : label}
        </Button>
        {estimate === undefined ? (
          failure ? null : (
            <p role="status" className="text-sm text-muted-foreground">
              Computing the estimate
            </p>
          )
        ) : (
          <EstimateLine estimate={estimate} />
        )}
        {text?.accepted ? (
          <p className="text-sm text-muted-foreground" data-testid="reminder">
            {text.reminder}
          </p>
        ) : null}
      </div>

      {estimate !== undefined ? (
        <Collapsible className="flex flex-col gap-2">
          <CollapsibleTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="w-fit px-1 text-muted-foreground"
            >
              <ChevronRight
                aria-hidden="true"
                className="transition-transform motion-reduce:transition-none [[data-state=open]_&]:rotate-90"
              />
              What is read, and the limits
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <EstimateView
              estimate={estimate}
              reread={reread}
              refused={refused}
              busy={estimating || starting}
              onReread={toggleReread}
            />
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </section>
  );
}
