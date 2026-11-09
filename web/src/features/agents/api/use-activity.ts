import { useQuery } from "@tanstack/react-query";

import {
  ACTIVITY_POLL_MS,
  ACTIVE_FLOW_STATUSES,
  getWorkflowRun,
  listWorkflowRuns,
  listWorkflows,
  workflowRunKey,
  workflowRunsKey,
  workflowsKey,
} from "./activity-api";

/** The workflows of the side list, in the api's order. */
export function useWorkflows() {
  return useQuery({
    queryKey: workflowsKey,
    queryFn: ({ signal }) => listWorkflows(signal),
    staleTime: 5 * 60_000,
  });
}

/** The person's workflow runs, newest first, read again while one is under way. */
export function useWorkflowRuns(workflow: string | null) {
  return useQuery({
    queryKey: workflowRunsKey(workflow),
    queryFn: ({ signal }) => listWorkflowRuns(workflow, signal),
    refetchInterval: (query) =>
      query.state.data?.some((run) => ACTIVE_FLOW_STATUSES.has(run.status))
        ? ACTIVITY_POLL_MS
        : false,
  });
}

/** One workflow run, its steps' statuses and durations read again while it is under way. */
export function useWorkflowRun(id: string) {
  return useQuery({
    queryKey: workflowRunKey(id),
    queryFn: ({ signal }) => getWorkflowRun(id, signal),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_FLOW_STATUSES.has(status)
        ? ACTIVITY_POLL_MS
        : false;
    },
  });
}
