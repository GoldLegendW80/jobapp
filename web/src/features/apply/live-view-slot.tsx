import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";

import {
  browserQueryKey,
  getBrowser,
  personOwnsInput,
  type Browser,
} from "@/features/browser/browser-api";
import { LiveBrowser } from "@/features/browser/live-browser";
import { HandoffBanner } from "@/features/browser/handoff-banner";

// How often the slot reads whether the assistant holds the browser (it ends, or starts).
const LEASE_POLL_MS = 3_000;

/** The purposes of an application's fill (api run.PURPOSE and run.FULL_AUTO_PURPOSE). */
const FILL_PURPOSES = new Set(["apply_fill", "full_auto"]);
/** The purpose of the submit lease (api submit.PURPOSE). */
const SUBMIT_PURPOSES = new Set(["apply_submit"]);

/**
 * Which of the person's leases is this application's: `any` lease of the slot's purposes (a
 * person has one lease at a time), one that started by `startedBy` (a later one is the fill of
 * another posting), one that started at or after `startedAfter` (an earlier one is another
 * application's), or `none`.
 */
export type LeaseOf =
  "any" | "none" | { startedBy: string } | { startedAfter: string };

/** The lease of this application, the one the live view shows; null otherwise. */
function applicationLease(
  browser: Browser | undefined,
  of: LeaseOf,
  purposes: ReadonlySet<string>,
) {
  const lease = browser?.holder === "you" ? browser.lease : null;
  if (!lease?.automation || lease.state === "starting" || of === "none")
    return null;
  if (!purposes.has(lease.purpose)) return null;
  if (of === "any") return lease;
  // browserd's started_at against the api's times: right only while both share the host clock.
  const started = Date.parse(lease.started_at);
  if ("startedAfter" in of)
    return started >= Date.parse(of.startedAfter) ? lease : null;
  return started <= Date.parse(of.startedBy) ? lease : null;
}

/**
 * Where the live view of the browser goes while the assistant fills this application's form, or
 * waits for the person (browser B4's LiveBrowser, apply A10a). Without this application's lease
 * it shows `idle` in words; the "before" screenshot appears below when the fill ends. `status` is
 * the sentence of the live view's status line. `stopped`: nothing runs, so no spinner. `submit`:
 * the submit lease's slot instead, watch only.
 */
export function LiveViewSlot({
  idle,
  leaseOf,
  status = "The assistant is filling the form.",
  stopped = false,
  submit = false,
}: {
  idle: string;
  leaseOf: LeaseOf;
  status?: string;
  stopped?: boolean;
  submit?: boolean;
}) {
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: LEASE_POLL_MS,
    enabled: leaseOf !== "none",
  });
  const lease = applicationLease(
    query.data,
    leaseOf,
    submit ? SUBMIT_PURPOSES : FILL_PURPOSES,
  );
  if (lease && submit) {
    return (
      <div data-testid="live-view-slot">
        <LiveBrowser key={lease.id} watchOnly status={status} />
      </div>
    );
  }
  if (lease) {
    if (lease.state === "waiting_person" && lease.wait_reason === "signin") {
      return <HandoffBanner liveOpen />;
    }
    const person = personOwnsInput(lease);
    return (
      <div data-testid="live-view-slot">
        <LiveBrowser
          key={`${lease.id}:${person}`}
          person={person}
          status={
            person
              ? "You have the keyboard and the mouse. Complete this step in the live view."
              : status
          }
        />
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
