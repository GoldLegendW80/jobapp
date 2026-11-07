import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getJobFeeds,
  jobFeedsKey,
  removeAdzunaKey,
  saveAdzunaKey,
} from "./job-feeds-api";

/** The job feeds and whether Adzuna's key is stored. */
export function useJobFeeds() {
  return useQuery({
    queryKey: jobFeedsKey,
    queryFn: ({ signal }) => getJobFeeds(signal),
    retry: false,
  });
}

/** Stores Adzuna's key, then reads the feeds again. */
export function useSaveAdzunaKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveAdzunaKey,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: jobFeedsKey });
    },
  });
}

/** Removes Adzuna's key, then reads the feeds again. */
export function useRemoveAdzunaKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: removeAdzunaKey,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: jobFeedsKey });
    },
  });
}
