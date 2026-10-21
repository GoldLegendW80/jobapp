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

/** The assistant's lease of this person, the one the live view shows; null otherwise. */
function assistantLease(browser: Browser | undefined) {
  const lease = browser?.holder === "you" ? browser.lease : null;
  return lease?.automation && lease.state !== "starting" ? lease : null;
}

/**
 * Where the live view of the browser goes while the assistant fills the form, or waits for the
 * person (browser B4's LiveBrowser, apply A10a). Until the assistant holds the browser this shows
 * the state in words; the "before" screenshot appears below when the fill ends. `status` is the
 * sentence of the live view's status line.
 */
export function LiveViewSlot({
  waiting,
  status = "The assistant is filling the form.",
}: {
  waiting: boolean;
  status?: string;
}) {
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: LEASE_POLL_MS,
  });
  const lease = assistantLease(query.data);
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
      <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
      <p>{waiting ? "The browser starts soon." : status}</p>
      <p>A screenshot of the filled form appears here when it is done.</p>
    </div>
  );
}
