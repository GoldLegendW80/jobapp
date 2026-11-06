/**
 * /runs, "Assistant activity": the user's agent runs, newest first, each named in words
 * (lib/wording.ts, agentWord), with status, agent, job, tokens, cost and
 * duration; each opens /runs/:id, the live RunPanel. Outside production the server also offers
 * test agents (GET /api/agents, `test_only`): then a "Test run" card starts one, which is how the
 * run panel is exercised before a real agent exists (A3 adds the first).
 */
import { ArrowLeft, Bot, CircleAlert } from "lucide-react";
import { Link, useParams } from "react-router";

import { LoadingCard } from "@/components/blocks/loading-card";
import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { useTitle } from "@/hooks/use-title";
import { describeError } from "@/lib/api";
import { WORDING, agentWord } from "@/lib/wording";

import { useAgents, useRun, useRuns } from "../api/use-runs";
import { RunJobLink } from "../components/run-job-link";
import { RunLiveView } from "../components/run-live-view";
import { RunPanel } from "@/components/shared/agents/run-panel";
import { RunRow } from "../components/run-row";
import { TestRunCard } from "../components/test-run-card";
import { useNow } from "../hooks/use-now";

export function RunsPage() {
  useTitle(WORDING.assistantActivity);
  const runs = useRuns();
  const agents = useAgents();
  const testAgents = (agents.data ?? []).filter((a) => a.test_only);
  const now = useNow(runs.data?.some((r) => r.status === "running") ?? false);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8">
      <PageHeader
        title={WORDING.assistantActivity}
        description="Everything the assistant did for you, with its tokens, its cost and how long it took. On a Claude subscription the amount is an estimate at API prices, not a bill: each run counts toward your plan's usage limits, and what you pay is set by your plan."
      />
      {testAgents.length ? <TestRunCard agents={testAgents} /> : null}
      <section aria-labelledby="runs-heading" className="flex flex-col gap-3">
        <h2 id="runs-heading" className="text-lg font-semibold">
          Runs
        </h2>
        {runs.isPending ? (
          <LoadingCard>Loading the activity</LoadingCard>
        ) : runs.isError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription className="flex flex-col items-start gap-3">
              <span>
                The activity could not be loaded. {describeError(runs.error)}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={runs.isFetching}
                aria-busy={runs.isFetching}
                onClick={() => void runs.refetch()}
              >
                {runs.isFetching ? (
                  <Spinner aria-hidden="true" role={undefined} />
                ) : null}
                {WORDING.tryAgain}
              </Button>
            </AlertDescription>
          </Alert>
        ) : runs.data.length === 0 ? (
          <Card>
            <Empty className="py-6 md:py-6">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Bot aria-hidden="true" />
                </EmptyMedia>
                <EmptyTitle>No activity yet</EmptyTitle>
                <EmptyDescription>
                  What the assistant does for you appears here.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          </Card>
        ) : (
          <Card className="gap-0 px-2 py-1">
            <ul aria-label="Runs" className="flex flex-col divide-y">
              {runs.data.map((run) => (
                <RunRow key={run.id} run={run} now={now} />
              ))}
            </ul>
          </Card>
        )}
      </section>
    </div>
  );
}

/** /runs/:id: one run, live; a website search's also shows the browser it reads (browser B18). */
export function RunPage() {
  const { id = "" } = useParams();
  // The same query as the panel's (shared cache): only read here for the job link.
  const detail = useRun(id);
  useTitle(
    detail.data ? agentWord(detail.data.agent) : WORDING.assistantActivity,
  );
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col items-start gap-3">
        <Button asChild variant="ghost" size="sm" className="-ml-3">
          <Link to="/runs">
            <ArrowLeft aria-hidden="true" />
            {WORDING.allActivity}
          </Link>
        </Button>
        <PageHeader
          title={
            detail.data
              ? agentWord(detail.data.agent)
              : WORDING.assistantActivity
          }
          description={
            detail.data?.job_id ? <RunJobLink run={detail.data} /> : null
          }
        />
      </div>
      <RunLiveView run={detail.data} />
      <RunPanel key={id} runId={id} title={WORDING.progress} />
    </div>
  );
}
