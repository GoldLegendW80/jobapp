import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  followUpToGmail,
  followUpsKey,
  getFollowUps,
  recordFollowUpSent,
  saveFollowUpText,
  saveFollowUpsOn,
  writeFollowUp,
  type FollowUp,
  type FollowUps,
} from "./followups-api";

/** While the assistant writes a draft, the list is read again this often. */
export const WRITING_POLL_MS = 3000;

/** The person's follow ups, read again while a draft is being written. */
export function useFollowUps() {
  return useQuery({
    queryKey: followUpsKey,
    queryFn: ({ signal }) => getFollowUps(signal),
    refetchInterval: (current) =>
      current.state.data?.items.some((item) => item.draft?.writing)
        ? WRITING_POLL_MS
        : false,
  });
}

/** One item answered by a click replaces its line in the cached list. */
function useReplaceItem() {
  const client = useQueryClient();
  return (item: FollowUp) => {
    client.setQueryData<FollowUps>(followUpsKey, (old) =>
      old
        ? {
            ...old,
            items: old.items.map((one) =>
              one.posting_id === item.posting_id ? item : one,
            ),
          }
        : old,
    );
    void client.invalidateQueries({ queryKey: followUpsKey });
  };
}

type Handlers<T, V> = Pick<
  UseMutationOptions<T, Error, V>,
  "onSuccess" | "onError"
>;

export function useWriteFollowUp(handlers: Handlers<FollowUp, string> = {}) {
  const replace = useReplaceItem();
  return useMutation({
    mutationFn: writeFollowUp,
    ...handlers,
    onSuccess: (item, ...rest) => {
      replace(item);
      return handlers.onSuccess?.(item, ...rest);
    },
  });
}

export function useSaveFollowUpText(
  postingId: string,
  handlers: Handlers<FollowUp, { subject: string; body: string }> = {},
) {
  const replace = useReplaceItem();
  return useMutation({
    mutationFn: (text: { subject: string; body: string }) =>
      saveFollowUpText(postingId, text),
    ...handlers,
    onSuccess: (item, ...rest) => {
      replace(item);
      return handlers.onSuccess?.(item, ...rest);
    },
  });
}

export function useRecordFollowUpSent(
  handlers: Handlers<FollowUp, string> = {},
) {
  const replace = useReplaceItem();
  return useMutation({
    mutationFn: recordFollowUpSent,
    ...handlers,
    onSuccess: (item, ...rest) => {
      replace(item);
      return handlers.onSuccess?.(item, ...rest);
    },
  });
}

export function useSaveFollowUpsOn() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveFollowUpsOn,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: followUpsKey });
    },
  });
}

export function useFollowUpToGmail(postingId: string) {
  return useMutation({
    mutationFn: (to: string) => followUpToGmail(postingId, to),
  });
}

/** After a send through Gmail, the list records the follow up as sent. */
export function useRefreshFollowUps() {
  const client = useQueryClient();
  return () => void client.invalidateQueries({ queryKey: followUpsKey });
}
