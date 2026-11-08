import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { FINAL_STATUSES, type Run } from "@/features/agents/api/runs-api";

import {
  assessmentKey,
  getAssessment,
  startAssessment,
  type AssessmentView,
} from "./assessment-api";

// While the agent works, the view is read again this often.
const POLL_MS = 2000;

export function isAssessing(view: AssessmentView | undefined): boolean {
  const status = view?.run?.status;
  return status !== undefined && !FINAL_STATUSES.has(status);
}

/** The posting's last assessment and its latest run, read again until the run ends. */
export function useAssessment(jobId: string) {
  return useQuery({
    queryKey: assessmentKey(jobId),
    queryFn: ({ signal }) => getAssessment(jobId, signal),
    refetchInterval: (query) =>
      isAssessing(query.state.data) ? POLL_MS : false,
  });
}

/** The Assess click: the run it answers becomes the posting's latest at once. */
export function useStartAssessment(
  jobId: string,
  handlers: Pick<UseMutationOptions<Run>, "onError">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => startAssessment(jobId),
    ...handlers,
    onSuccess: (run) => {
      client.setQueryData<AssessmentView>(assessmentKey(jobId), (view) => ({
        assessment: view?.assessment ?? null,
        run: { id: run.id, status: run.status, error: run.error },
      }));
    },
  });
}
