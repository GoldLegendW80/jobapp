/**
 * /runs, "Assistant activity": the user's agent runs, newest first, each named in words
 * (lib/wording.ts, agentWord), with status, posting, tokens, cost and duration; each opens
 * /runs/:id, the run live. Outside production the server also offers test agents
 * (GET /api/agents, `test_only`): then a "Test run" section starts one, which is how the run
 * page is exercised before a real agent exists (A3 adds the first).
 */
import { Bot, Info, SearchX } from "lucide-react";
import { useRef } from "react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/features/jobs/helpers/format";
import { useTitle } from "@/hooks/use-title";
import { WORDING, agentWord } from "@/lib/wording";

import { useRunStream } from "../api/use-run-stream";
import { useAgents, useRuns } from "../api/use-runs";
import { RunFacts } from "../components/run-facts";
import { RunLiveView } from "../components/run-live-view";
import { RunsTable, RunsTableLoading } from "../components/runs-table";
import { TestRunCard } from "../components/test-run-card";
import { useNow } from "../hooks/use-now";
import { useReturnFocus } from "../hooks/use-return-focus";
import { CancelRunButton } from "@/components/shared/agents/run/cancel-run-button";
import { ConnectionState } from "@/components/shared/agents/run/connection-state";
import { RunTimeline } from "@/components/shared/agents/run/run-timeline";

const TRAIL = [{ to: "/runs", label: "Activity" }];

export function RunsPage() {
  useTitle(WORDING.assistantActivity);
  const runs = useRuns();
  const agents = useAgents();
  const testAgents = (agents.data ?? []).filter((a) => a.test_only);
  const now = useNow(runs.data?.some((r) => r.status === "running") ?? false);
  return (
    <Page>
      <PageHeader
        title={WORDING.assistantActivity}
        description="Everything the assistant did for you, with its tokens, its cost and how long it took."
      />
      <PageBody>
        {runs.isPending ? (
          <RunsTableLoading />
        ) : runs.isError ? (
          <LoadError query={runs}>The activity could not be loaded.</LoadError>
        ) : runs.data.length === 0 ? (
          <EmptyState
            icon={Bot}
            title="No activity yet"
            description="What the assistant does for you appears here."
          />
        ) : (
          <div className="flex flex-col gap-3">
            <RunsTable runs={runs.data} now={now} />
            <p className="flex max-w-prose gap-2 text-xs text-muted-foreground">
              <Info aria-hidden="true" className="mt-px size-3.5 shrink-0" />
              <span>
                On a Claude subscription the amount is an estimate at API
                prices, not a bill: each run counts toward your plan&apos;s
                usage limits, and what you pay is set by your plan.
              </span>
            </p>
          </div>
        )}
        {testAgents.length ? <TestRunCard agents={testAgents} /> : null}
      </PageBody>
    </Page>
  );
}

/**
 * /runs/:id: one run, live. Its steps in the main column (a website search's also shows the
 * browser it reads, browser B18), its facts in the side rail, Cancel in the header while it works.
 */
export function RunPage() {
  const { id = "" } = useParams();
  const { detail, steps, live, status, connection, gone } = useRunStream(id);
  const run = detail.data;
  const shownStatus = status ?? run?.status;
  const active = shownStatus === "queued" || shownStatus === "running";
  const now = useNow(active);
  const titleRef = useRef<HTMLHeadingElement>(null);
  // Cancel goes away when the run ends: its focus goes to the title, not to the page.
  const cancelFocus = useReturnFocus(active, titleRef);
  useTitle(run ? agentWord(run.agent) : WORDING.assistantActivity);

  if (gone || !run || !shownStatus) {
    return (
      <Page>
        <PageHeader trail={TRAIL} title={WORDING.assistantActivity} />
        {gone ? (
          <EmptyState
            icon={SearchX}
            title="This run no longer exists"
            actions={
              <Button asChild variant="outline">
                <Link to="/runs">{WORDING.allActivity}</Link>
              </Button>
            }
          />
        ) : detail.isPending ? (
          <LoadingCard>Loading the run</LoadingCard>
        ) : (
          <LoadError query={detail}>The run could not be loaded.</LoadError>
        )}
      </Page>
    );
  }
  return (
    // One element holds the run's parts, header included: the journeys follow a run in it.
    <div data-testid="run-panel" data-status={shownStatus}>
      <Page>
        <PageHeader
          trail={TRAIL}
          titleRef={titleRef}
          title={agentWord(run.agent)}
          description={`Started ${formatDateTime(run.created_at)}`}
          actions={
            active ? (
              <CancelRunButton
                runId={id}
                stopping={run.cancel_requested}
                {...cancelFocus}
              />
            ) : null
          }
        />
        <PageBody
          aside={
            <RunFacts
              run={run}
              status={shownStatus}
              now={now}
              refreshFailed={detail.isRefetchError}
            />
          }
        >
          <RunLiveView run={run} />
          <Section
            title="Steps"
            description={<ConnectionState state={connection} />}
          >
            <Card>
              <CardContent>
                <RunTimeline
                  steps={steps}
                  live={live}
                  kind={run.credential_kind}
                />
              </CardContent>
            </Card>
          </Section>
        </PageBody>
      </Page>
    </div>
  );
}
