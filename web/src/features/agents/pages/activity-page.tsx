/**
 * /activity and /activity/:workflow, "Activity": the person's workflow runs in the pattern of
 * GitHub Actions. The workflows on the side (All workflows, then each), the runs of the one
 * chosen in the middle, newest first, each opening its run page. Outside production a "Test
 * run" section starts a test agent, as before.
 */
import { Bot, Info } from "lucide-react";
import { useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { ACTIVE_FLOW_STATUSES } from "../api/activity-api";
import { useWorkflowRuns, useWorkflows } from "../api/use-activity";
import { useAgents } from "../api/use-runs";
import { TestRunCard } from "../components/test-run-card";
import { WorkflowNav } from "../components/workflow-nav";
import {
  WorkflowRunList,
  WorkflowRunListLoading,
} from "../components/workflow-run-list";
import { useNow } from "../hooks/use-now";

export function ActivityPage() {
  const { workflow: key = null } = useParams();
  const workflows = useWorkflows();
  const runs = useWorkflowRuns(key);
  const agents = useAgents();
  const testAgents = (agents.data ?? []).filter((a) => a.test_only);
  const now = useNow(
    runs.data?.some((run) => ACTIVE_FLOW_STATUSES.has(run.status)) ?? false,
  );
  const workflow = workflows.data?.find((one) => one.key === key);
  const title = workflow?.title ?? WORDING.allWorkflows;
  useTitle(key ? `${title}, ${WORDING.activity}` : WORDING.activity);
  return (
    <Page>
      <PageHeader
        title={WORDING.activity}
        description="Everything the assistant and the app did for you, run by run: what started it, where it is, and the log of each step."
      />
      <div className="grid min-w-0 gap-6 md:grid-cols-[14rem_minmax(0,1fr)]">
        <WorkflowNav
          workflows={workflows.data ?? []}
          loading={workflows.isPending}
        />
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="text-lg font-semibold">{title}</h2>
            {workflow?.sentence ? (
              <p className="max-w-prose text-sm text-muted-foreground">
                {workflow.sentence}
              </p>
            ) : null}
          </div>
          {runs.isPending ? (
            <WorkflowRunListLoading />
          ) : runs.isError ? (
            <LoadError query={runs}>
              The activity could not be loaded.
            </LoadError>
          ) : runs.data.length === 0 ? (
            <EmptyState
              icon={Bot}
              title={key ? "No run of this workflow yet" : "No activity yet"}
              description="What the assistant and the app do for you appears here, run by run."
            />
          ) : (
            <>
              <WorkflowRunList runs={runs.data} now={now} showWorkflow={!key} />
              <p className="flex max-w-prose gap-2 text-xs text-muted-foreground">
                <Info aria-hidden="true" className="mt-px size-3.5 shrink-0" />
                <span>
                  On a Claude subscription the amount of a run is an estimate at
                  API prices, not a bill: each run counts toward your
                  plan&apos;s usage limits, and what you pay is set by your
                  plan.
                </span>
              </p>
            </>
          )}
          {testAgents.length && !key ? (
            <TestRunCard agents={testAgents} />
          ) : null}
        </div>
      </div>
    </Page>
  );
}
