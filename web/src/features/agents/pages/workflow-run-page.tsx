/**
 * /activity/runs/:id: one workflow run, as a GitHub Actions run page. Its summary (trigger, who,
 * status, total duration, tokens, cost), the workflow's graph with each step's status and
 * duration, its steps listed on the side, and the log of the agent step chosen (`?step=`), live.
 * While the run goes on its statuses and durations are read again by themselves, and the steps
 * still to come show as waiting. `:id` may be an agent run's id (an old /runs/:id link): the
 * page then moves to its workflow run's address with that step open.
 */
import { SearchX } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import { WorkflowDiagram } from "@/components/shared/agents/workflow-diagram";
import { isApiError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { ACTIVE_FLOW_STATUSES } from "../api/activity-api";
import { useWorkflowRun } from "../api/use-activity";
import { AgentStepLog } from "../components/agent-step-log";
import { WorkflowRunSteps } from "../components/workflow-run-steps";
import { WorkflowRunSummary } from "../components/workflow-run-summary";
import { nodeStates, stepHref } from "../helpers/activity";
import { formatDateTime } from "@/lib/format";
import { useNow } from "../hooks/use-now";

export function WorkflowRunPage() {
  const { id = "" } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const query = useWorkflowRun(id);
  const run = query.data;
  const active = run ? ACTIVE_FLOW_STATUSES.has(run.status) : false;
  const now = useNow(active);
  const titleRef = useRef<HTMLHeadingElement>(null);
  useTitle(run ? (run.subject?.words ?? run.workflow_title) : WORDING.activity);

  const agentSteps = run?.steps.filter((step) => step.kind === "agent") ?? [];
  const asked = search.get("step");
  // The step asked for, else the step at work, else the last one that ran.
  const selected =
    agentSteps.find((step) => step.id === asked)?.id ??
    agentSteps.find((step) => ACTIVE_FLOW_STATUSES.has(step.status))?.id ??
    agentSteps.at(-1)?.id ??
    null;

  useEffect(() => {
    if (run && run.id !== id) {
      void navigate(stepHref(run.id, asked ?? id), { replace: true });
    }
  }, [run, id, asked, navigate]);

  const trail = [
    { to: "/activity", label: WORDING.activity },
    ...(run
      ? [
          {
            to: `/activity/${encodeURIComponent(run.workflow)}`,
            label: run.workflow_title,
          },
        ]
      : []),
  ];

  if (!run) {
    const gone = isApiError(query.error) && query.error.status === 404;
    return (
      <Page>
        <PageHeader trail={trail} title={WORDING.activity} />
        {gone ? (
          <EmptyState
            icon={SearchX}
            title="This run no longer exists"
            actions={
              <Button asChild variant="outline">
                <Link to="/activity">{WORDING.allWorkflows}</Link>
              </Button>
            }
          />
        ) : query.isPending ? (
          <LoadingCard>Loading the run</LoadingCard>
        ) : (
          <LoadError query={query}>The run could not be loaded.</LoadError>
        )}
      </Page>
    );
  }
  return (
    <Page>
      <PageHeader
        trail={trail}
        titleRef={titleRef}
        title={run.subject?.words ?? run.workflow_title}
        description={`${run.workflow_title}, started ${formatDateTime(run.created_at)}`}
      />
      <div
        className="flex min-w-0 flex-col gap-6"
        data-workflow-status={run.status}
      >
        <WorkflowRunSummary run={run} now={now} />
        {query.isRefetchError ? (
          <p className="text-xs text-warning" role="status">
            Could not refresh this run; it tries again in a moment.
          </p>
        ) : null}
        {run.flow ? (
          <WorkflowDiagram
            title={run.workflow_title}
            flow={run.flow}
            runs={nodeStates(run, now)}
            linkAgents={false}
          />
        ) : null}
        <div className="grid min-w-0 items-start gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <WorkflowRunSteps
            runId={run.id}
            steps={run.steps}
            selected={selected}
            now={now}
          />
          {selected ? (
            <AgentStepLog key={selected} runId={selected} titleRef={titleRef} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No agent has worked in this run yet.
            </p>
          )}
        </div>
      </div>
    </Page>
  );
}
