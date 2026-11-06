import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useEffect } from "react";

import type { Application } from "./apply-api";
import {
  actManual,
  closeManual,
  getManual,
  manualKey,
  openManual,
  recordManual,
  type ManualAction,
  type ManualStatus,
} from "./manual-api";
import { APPLY_POLL_MS } from "../helpers/apply-state";
import { REOPEN_MS } from "../helpers/manual-state";

/** Apply myself's session of one application, read while it is `open`. */
export function useManualStatus(applicationId: string, open: boolean) {
  return useQuery({
    queryKey: manualKey(applicationId),
    queryFn: ({ signal }) => getManual(applicationId, signal),
    enabled: open,
    refetchInterval: open ? APPLY_POLL_MS : false,
  });
}

/**
 * While `queued`, asks for the session at once and again every REOPEN_MS, to keep the place
 * while it waits for the browser. `onError` must keep its identity across renders.
 */
export function useOpenManual(
  applicationId: string,
  queued: boolean,
  onError: (error: unknown) => void,
) {
  const client = useQueryClient();
  useEffect(() => {
    if (!queued) return;
    const ask = () => {
      openManual(applicationId).then(
        (found) => client.setQueryData(manualKey(applicationId), found),
        onError,
      );
    };
    ask();
    const timer = window.setInterval(ask, REOPEN_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [queued, applicationId, client, onError]);
}

/** Fill the identity fields or attach a document; the session it answers is kept. */
export function useManualAct(
  applicationId: string,
  handlers: Pick<
    UseMutationOptions<ManualStatus, Error, ManualAction>,
    "onMutate" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: ManualAction) => actManual(applicationId, action),
    ...handlers,
    onSuccess: (found) => {
      client.setQueryData(manualKey(applicationId), found);
    },
  });
}

/** Ends the session: recorded as sent (`seen` on a confirmation page, or `sent`), or `close`d. */
export function useFinishManual(
  applicationId: string,
  handlers: Pick<
    UseMutationOptions<Application, Error, "seen" | "sent" | "close">,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  return useMutation({
    mutationFn: (how: "seen" | "sent" | "close") =>
      how === "close"
        ? closeManual(applicationId)
        : recordManual(applicationId, how === "seen"),
    ...handlers,
  });
}
