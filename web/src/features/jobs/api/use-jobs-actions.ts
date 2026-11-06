import {
  useMutation,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  addJob,
  bulkUpdate,
  jobsKey,
  rescoreAll,
  type BulkResult,
  type JobDetail,
  type NewPosting,
  type RescoreResult,
} from "./jobs-api";

/** Adds a posting by hand, then reads the postings again. */
export function useAddPosting(
  handlers: Pick<
    UseMutationOptions<JobDetail, Error, NewPosting>,
    "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (posting: NewPosting) => addJob(posting),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: jobsKey });
      return handlers.onSuccess?.(...args);
    },
  });
}

type RescoreOptions = Parameters<typeof rescoreAll>[0];

/**
 * Rescores the postings `ids`, or all of them, in as many calls as it takes, with each step's
 * totals to `onProgress`; the postings are read again whatever the outcome.
 */
export function useRescore({
  onProgress,
  onSuccess,
  onError,
}: { onProgress: (done: RescoreResult) => void } & Pick<
  UseMutationOptions<RescoreResult, Error, RescoreOptions>,
  "onSuccess" | "onError"
>) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: jobsKey });
  };
  return useMutation({
    mutationFn: (options: RescoreOptions) => rescoreAll(options, onProgress),
    onSuccess: (...args) => {
      refresh();
      return onSuccess?.(...args);
    },
    onError: (...args) => {
      refresh();
      return onError?.(...args);
    },
  });
}

/** Appends one note to the notes of each posting of `ids`. */
export function useBulkNote(
  handlers: Pick<
    UseMutationOptions<BulkResult, Error, { ids: string[]; note: string }>,
    "onSuccess" | "onError"
  >,
) {
  return useMutation({
    mutationFn: ({ ids, note }: { ids: string[]; note: string }) =>
      bulkUpdate(ids, { notes_append: note }),
    ...handlers,
  });
}
