import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";

import {
  browserQueryKey,
  getBrowser,
  type Browser,
} from "@/features/browser/browser-api";
import { LiveBrowser } from "@/features/browser/live-browser";

// How often the slot reads whether the assistant holds the browser (it ends, or starts).
const LEASE_POLL_MS = 3_000;

/** The purposes of an application's fill (api run.PURPOSE and run.FULL_AUTO_PURPOSE). */
const FILL_PURPOSES = new Set(["apply_fill", "full_auto"]);

/**
 * Which of the person's leases is this application's: `any` fill lease (the application fills,
 * and a person has one fill at a time), one that started by `startedBy` (a later one is the fill
 * of another posting), or `none`.
 */
export type LeaseOf = "any" | "none" | { startedBy: string };

/** The lease of this application's fill, the one the live view shows; null otherwise. */
function applicationLease(browser: Browser | undefined, of: LeaseOf) {
  const lease = browser?.holder === "you" ? browser.lease : null;
  if (!lease?.automation || lease.state === "starting" || of === "none")
    return null;
  if (!FILL_PURPOSES.has(lease.purpose)) return null;
  if (of === "any") return lease;
  const started = Date.parse(lease.started_at);
  const by = Date.parse(of.startedBy);
  return started <= by ? lease : null;
}

/**
 * Where the live view of the browser goes while the assistant fills this application's form, or
 * waits for the person (browser B4's LiveBrowser, apply A10a). Without this application's lease
 * it shows `idle` in words; the "before" screenshot appears below when the fill ends. `status` is
 * the sentence of the live view's status line. `stopped`: nothing runs, so no spinner.
 */
export function LiveViewSlot({
  idle,
  leaseOf,
  status = "The assistant is filling the form.",
  stopped = false,
}: {
  idle: string;
  leaseOf: LeaseOf;
  status?: string;
  stopped?: boolean;
}) {
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: LEASE_POLL_MS,
    enabled: leaseOf !== "none",
  });
  const lease = applicationLease(query.data, leaseOf);
  if (lease) {
    return (
      <div data-testid="live-view-slot">
        <LiveBrowser key={lease.id} status={status} />
      </div>
    );
  }
  return (
    <div
      data-testid="live-view-slot"
      className="flex min-h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground"
    >
      {stopped ? null : (
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
      )}
      <p>{idle}</p>
    </div>
  );
}
