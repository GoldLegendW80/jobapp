import { useMutation, useQueryClient } from "@tanstack/react-query";

import { isStale, jobKey, jobsKey, patchJob, type JobDetail } from "./jobs-api";

/** Moves a posting to Closed, on the version last read; a version changed elsewhere is read again. */
export function useCloseJob(job: Pick<JobDetail, "id" | "updated_at">) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      patchJob(job.id, { status: "closed", updated_at: job.updated_at }),
    onSuccess: (updated) => {
      client.setQueryData(jobKey(job.id), updated);
      void client.invalidateQueries({ queryKey: jobsKey });
    },
    onError: (error) => {
      // Changed in another tab: read it again, so a second click carries its `updated_at`.
      if (isStale(error))
        void client.invalidateQueries({ queryKey: jobKey(job.id) });
    },
  });
}
