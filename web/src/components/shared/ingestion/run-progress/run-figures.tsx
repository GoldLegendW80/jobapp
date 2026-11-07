import type { ReactNode } from "react";

import { isActive, tokens, type Run } from "@/features/ingestion/api/runs-api";
import {
  countOf,
  reusedDocuments,
} from "@/features/ingestion/helpers/run-words";

/** The figures of a run: steps or documents read, what was found, the tokens and the cost. */
export function RunFigures({ run }: { run: Run }) {
  const active = isActive(run.status);
  const reused = reusedDocuments(run);
  return (
    <>
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
            {reused > 0
              ? `, ${tokens(reused)} reused from an earlier run`
              : null}
          </Figure>
        )}
        <Figure label="Found so far">
          {countOf(run.counts.items, "item", "items")}
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
            {countOf(run.counts.proposals, "proposal", "proposals")} to review
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
    </>
  );
}

function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </div>
  );
}
