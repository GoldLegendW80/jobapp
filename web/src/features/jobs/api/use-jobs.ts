import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query";

import { countJobs, jobCountKey, jobListKey, listJobs } from "./jobs-api";

/** The postings of one list query (`listQuery(filters)`), page by page; the previous view stays
 * on screen while a new filter loads. */
export function useJobs(apiQuery: string) {
  return useInfiniteQuery({
    queryKey: jobListKey(apiQuery),
    queryFn: ({ pageParam, signal }) => listJobs(apiQuery, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/** How many postings the person has, whatever their status. */
export function useJobCount(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobCountKey,
    queryFn: ({ signal }) => countJobs(signal),
    ...options,
  });
}
