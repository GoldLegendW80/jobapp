import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  browserQueryKey,
  forgetLogins,
  getBrowser,
  giveBack,
  liveTicket,
  takeOver,
  type Browser,
} from "./browser-api";

/**
 * The person's browser (GET /api/browser). Each place reads it as often as what it shows needs:
 * `options` holds when it reads and how often.
 */
export function useBrowser(
  options: Pick<
    UseQueryOptions<Browser, Error, Browser, typeof browserQueryKey>,
    "enabled" | "refetchInterval" | "refetchOnMount"
  > = {},
) {
  return useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    ...options,
  });
}

/**
 * The browser as read since the calling component mounted, every `refetchInterval`: undefined
 * until a read succeeds, since the cache may hold the lease of an earlier one.
 */
export function useFreshBrowser(refetchInterval: number): Browser | undefined {
  const client = useQueryClient();
  // The successful reads of the browser before this mount: a failed read keeps the cache.
  const [before] = useState(
    () => client.getQueryState(browserQueryKey)?.dataUpdateCount ?? 0,
  );
  const query = useBrowser({ refetchInterval, refetchOnMount: "always" });
  // `dataUpdatedAt` is read so that every successful read renders again.
  const fresh =
    query.dataUpdatedAt > 0 &&
    (client.getQueryState(browserQueryKey)?.dataUpdateCount ?? 0) > before;
  return fresh ? query.data : undefined;
}

/** "Forget all logins": the browser is read again however it ends. */
export function useForgetLogins(
  handlers: Pick<UseMutationOptions<void>, "onSuccess">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: forgetLogins,
    ...handlers,
    onSettled: () => {
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });
}

/**
 * The calls of one live view, `viewer`, of the browser's window `slot`: the one use ticket of its
 * screen, and taking the keyboard and the mouse from the assistant or giving them back, each
 * answered with who holds them now.
 */
export function useLiveView(viewer: string, slot = 0) {
  return useMemo(
    () => ({
      ticket: () => liveTicket(viewer, slot),
      takeOver: () => takeOver(viewer, slot),
      giveBack: () => giveBack(viewer, slot),
    }),
    [viewer, slot],
  );
}
