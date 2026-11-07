import { useQuery } from "@tanstack/react-query";

import { fetchFileJson, getJob, jobKey, jobsKey } from "./jobs-api";

/** One posting (GET /api/jobs/{id}): the posting page and the Jobs list's detail pane share it. */
export function useJob(id: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKey(id),
    queryFn: ({ signal }) => getJob(id, signal),
    ...options,
  });
}

/** A JSON file of a posting, parsed; read once, it never goes stale. */
export function useJobFileJson(
  jobId: string,
  fileId: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [...jobsKey, "file", jobId, fileId],
    queryFn: ({ signal }) => fetchFileJson(jobId, fileId, signal),
    ...options,
    staleTime: Infinity,
  });
}
