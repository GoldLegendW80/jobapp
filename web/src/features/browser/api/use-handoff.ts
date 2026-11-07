import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  browserQueryKey,
  getBrowser,
  handoffCancel,
  handoffDone,
  leasesOf,
  type Browser,
} from "./browser-api";

// While an assistant drives the person's browser, a sign in hand off may begin at any moment.
const WATCH_MS = 5_000;

export type Handoff = {
  host: string;
  minutesLeft: number | null;
  // The window of the person's browser the waiting lease uses (two browsers at once).
  slot: number;
};

/**
 * The sign in hand off waiting for this person (browser B12a's state in GET /api/browser): the
 * host browserd noted for it, the whole minutes left and its window, or null. Only one of the
 * person's own leases, in any window, driven by an assistant and waiting for a sign in (`leaseId`:
 * that lease only); never another person's (the api says `holder: "other"` and no lease).
 */
export function handoffOf(
  browser: Browser | undefined,
  leaseId?: string,
): Handoff | null {
  const lease = leasesOf(browser).find(
    (held) =>
      (leaseId === undefined || held.id === leaseId) &&
      held.automation &&
      held.state === "waiting_person" &&
      held.wait_reason === "signin",
  );
  if (!lease) return null;
  const minutesLeft = lease.wait_minutes_left ?? null;
  if (minutesLeft !== null && minutesLeft <= 0) return null;
  return {
    host: lease.wait_host?.trim() || "the site",
    minutesLeft,
    slot: lease.slot ?? 0,
  };
}

function watched(browser: Browser | undefined): boolean {
  return leasesOf(browser).some((lease) => lease.automation);
}

/**
 * The hand off of the banner, wherever it shows (Account, and apply's job page and queue): the
 * state, read again every few seconds while an assistant drives the person's browser, and "I'm
 * signed in" and "Cancel". The app shell's dot is apply A10a's. `leaseId`: that lease's only.
 */
export function useHandoff(leaseId?: string) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: (current) =>
      watched(current.state.data) ? WATCH_MS : false,
  });
  const refresh = () => client.invalidateQueries({ queryKey: browserQueryKey });
  const done = useMutation({ mutationFn: handoffDone, onSettled: refresh });
  const cancel = useMutation({
    mutationFn: handoffCancel,
    onSettled: refresh,
  });
  return { handoff: handoffOf(query.data, leaseId), done, cancel };
}
