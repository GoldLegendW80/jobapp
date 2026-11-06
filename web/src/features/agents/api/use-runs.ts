import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  agentsKey,
  FINAL_STATUSES,
  getRun,
  jobRunsKey,
  latestJobRun,
  listAgents,
  listRuns,
  runKey,
  runsKey,
  startRun,
  type Run,
} from "./runs-api";

// While a run is queued or running, the list is read again this often.
const RUNS_POLL_MS = 3000;

/** The person's runs, newest first, read again while one is queued or running. */
export function useRuns() {
  return useQuery({
    queryKey: runsKey,
    queryFn: ({ signal }) => listRuns(signal),
    refetchInterval: (query) =>
      query.state.data?.some(
        (r) => r.status === "queued" || r.status === "running",
      )
        ? RUNS_POLL_MS
        : false,
  });
}

/** The agents the person can run (test agents outside production too). */
export function useAgents() {
  return useQuery({
    queryKey: agentsKey,
    queryFn: ({ signal }) => listAgents(signal),
  });
}

/** One run's detail. */
export function useRun(id: string) {
  return useQuery({
    queryKey: runKey(id),
    queryFn: ({ signal }) => getRun(id, signal),
  });
}

/** The latest run of `agent` on one job, or null; read every `pollMs` until it ends. */
export function useLatestJobRun(jobId: string, agent: string, pollMs: number) {
  return useQuery({
    queryKey: jobRunsKey(jobId, agent),
    queryFn: ({ signal }) => latestJobRun(jobId, agent, signal),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status !== undefined && !FINAL_STATUSES.has(status)
        ? pollMs
        : false;
    },
  });
}

type NewRun = Parameters<typeof startRun>[0];

/** Starts a run; the list of runs is read again. */
export function useStartRun(
  handlers: Pick<
    UseMutationOptions<Run, Error, NewRun>,
    "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: NewRun) => startRun(body),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
      return handlers.onSuccess?.(...args);
    },
  });
}

/** Starts a run of `agent` on one job: it becomes the job's latest run at once. */
export function useStartJobRun(
  jobId: string,
  agent: string,
  handlers: Pick<UseMutationOptions<Run>, "onError">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => startRun({ agent, job_id: jobId, input: {} }),
    onSuccess: (started) => {
      client.setQueryData(jobRunsKey(jobId, agent), started);
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    ...handlers,
  });
}
