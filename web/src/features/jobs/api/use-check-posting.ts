import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "@/lib/api";

import { jobDetailSchema, jobKey, jobsKey, type JobDetail } from "./jobs-api";

/** Asks the posting's own address now (POST /api/jobs/{id}/check): a page that says it is closed closes it. */
export function checkPosting(id: string): Promise<JobDetail> {
  return request(`/jobs/${encodeURIComponent(id)}/check`, {
    method: "POST",
    timeoutMs: 30_000,
    validate: (data) => jobDetailSchema.parse(data),
  });
}

/** "Check now" on a posting: the posting as the check left it replaces the one shown. */
export function useCheckPosting(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => checkPosting(id),
    onSuccess: (job) => {
      client.setQueryData(jobKey(id), job);
      void client.invalidateQueries({ queryKey: jobsKey });
    },
  });
}
