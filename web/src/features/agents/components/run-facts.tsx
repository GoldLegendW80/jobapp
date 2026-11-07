import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";

import type { Run, RunStatus } from "../api/runs-api";
import { RunJobLink } from "./run-job-link";
import { Metric, RunMetrics } from "@/components/shared/agents/run/run-metrics";
import { RunStatusBadge } from "@/components/shared/agents/run/run-status-badge";

/** The side rail of a run's page: its status, model, tokens, amount, duration and posting. */
export function RunFacts({
  run,
  status,
  now,
  refreshFailed,
}: {
  run: Run;
  /** The newest status, from the steps when they are ahead of the detail. */
  status: RunStatus;
  now: number;
  refreshFailed: boolean;
}) {
  return (
    <Section title="Details">
      <Card>
        <CardContent className="flex flex-col gap-4">
          <RunMetrics
            run={run}
            now={now}
            className="@lg:grid-cols-2"
            before={
              <Metric
                label="Status"
                value={<RunStatusBadge status={status} />}
                className="col-span-2"
              />
            }
            after={
              run.job_id ? (
                <Metric
                  label="Posting"
                  value={<RunJobLink run={run} />}
                  className="col-span-2"
                />
              ) : null
            }
          />
          {refreshFailed ? (
            <p className="text-xs text-warning">
              Could not refresh the totals; they update with the next step.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
