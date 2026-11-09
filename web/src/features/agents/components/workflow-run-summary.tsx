import { Link } from "react-router";

import { Fact, Facts } from "@/components/blocks/facts";
import { Card, CardContent } from "@/components/ui/card";

import type { WorkflowRunDetail } from "../api/activity-api";
import {
  FLOW_STATUS_WORDS,
  durationWords,
  secondsOf,
} from "../helpers/activity";
import {
  costLabel,
  costNote,
  formatCost,
  formatTokens,
} from "../helpers/format";
import { FlowStatusIcon } from "./flow-status-icon";

/** The summary over a workflow run: its trigger, who, status, total duration, tokens and cost. */
export function WorkflowRunSummary({
  run,
  now,
}: {
  run: WorkflowRunDetail;
  now: number;
}) {
  const kind = run.credential_kind;
  const note = costNote(kind);
  return (
    <Card>
      <CardContent className="@container">
        <Facts className="grid-cols-2 @lg:grid-cols-3 @3xl:grid-cols-6">
          <Fact label="Triggered by" className="col-span-2 @lg:col-span-1">
            {run.trigger.words}
          </Fact>
          <Fact label="Who">You</Fact>
          <Fact label="Status">
            <span className="flex items-center gap-1.5">
              <FlowStatusIcon status={run.status} />
              <span aria-hidden="true">{FLOW_STATUS_WORDS[run.status]}</span>
            </span>
          </Fact>
          <Fact label="Total duration">
            {durationWords(secondsOf(run, now)) ?? "Not started"}
          </Fact>
          <Fact label="Tokens">
            {formatTokens(run.input_tokens, run.output_tokens)}
          </Fact>
          <Fact label={costLabel(kind)}>{formatCost(run.cost_usd)}</Fact>
          {run.subject ? (
            <Fact label="About" className="col-span-2 @3xl:col-span-6">
              {run.subject.job_id ? (
                <Link
                  to={`/jobs/${encodeURIComponent(run.subject.job_id)}`}
                  className="underline-offset-4 hover:underline"
                >
                  {run.subject.words}
                </Link>
              ) : (
                run.subject.words
              )}
            </Fact>
          ) : null}
        </Facts>
        {note && run.cost_usd !== null ? (
          <p className="mt-4 max-w-prose text-xs text-muted-foreground">
            {note}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
