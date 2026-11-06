import { useQuery } from "@tanstack/react-query";

import { applicationsKey, latestApplication } from "./apply-api";

/** The latest application of a posting as the cache holds it: this never reads, the Apply
 * section's own query (use-application.ts) does. */
export function useCachedLatestApplication(postingId: string) {
  return useQuery({
    queryKey: applicationsKey(postingId),
    queryFn: ({ signal }) => latestApplication(postingId, signal),
    enabled: false,
  });
}
