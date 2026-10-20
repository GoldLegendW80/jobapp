import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  browserQueryKey,
  getBrowser,
  handoffCancel,
  handoffDone,
  type Browser,
} from "./browser-api";

// While an assistant drives the person's browser, a sign in hand off may begin at any moment.
const WATCH_MS = 5_000;

export type Handoff = { site: string; minutesLeft: number | null };

/**
 * The sign in hand off waiting for this person (browser B12a's state in GET /api/browser): the
 * site and the whole minutes left, or null. Only the person's own lease, driven by an assistant,
 * waiting for a sign in; never another person's (the api says `holder: "other"` and no lease).
 */
export function handoffOf(browser: Browser | undefined): Handoff | null {
  const lease = browser?.holder === "you" ? browser.lease : null;
  if (
    !lease?.automation ||
    lease.state !== "waiting_person" ||
    lease.wait_reason !== "signin"
  )
    return null;
  const minutesLeft = lease.wait_minutes_left ?? null;
  if (minutesLeft !== null && minutesLeft <= 0) return null;
  return { site: lease.wait_site?.trim() || "the site", minutesLeft };
}

function watched(browser: Browser | undefined): boolean {
  return browser?.holder === "you" && !!browser.lease?.automation;
}

/**
 * The hand off of the banner, wherever it shows (Account, and apply's job page and queue): the
 * state, read again every few seconds while an assistant drives the person's browser, and "I'm
 * signed in" and "Cancel". The app shell's dot is apply A10a's.
 */
export function useHandoff() {
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
  return { handoff: handoffOf(query.data), done, cancel };
}
