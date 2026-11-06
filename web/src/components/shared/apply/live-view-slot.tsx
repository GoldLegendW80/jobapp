import { Empty, EmptyDescription, EmptyMedia } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import {
  personOwnsInput,
  type Browser,
} from "@/features/browser/api/browser-api";
import { useBrowser } from "@/features/browser/api/use-browser";
import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { HandoffBanner } from "@/components/shared/browser/handoff-banner";

// How often the slot reads whether the assistant holds the browser (it ends, or starts).
const LEASE_POLL_MS = 3_000;

/** The purposes of an application's fill (api run.PURPOSE and run.FULL_AUTO_PURPOSE). */
const FILL_PURPOSES = new Set(["apply_fill", "full_auto"]);
/** The purpose of the submit lease (api submit.PURPOSE). */
const SUBMIT_PURPOSES = new Set(["apply_submit"]);

/**
 * Which of the person's leases is this application's: `any` fill lease (the application fills,
 * and a person has one fill at a time), one that started by `startedBy` (a later one is the fill
 * of another posting), the one the api names for `application` (the submit lease), or `none`.
 */
export type LeaseOf =
  "any" | "none" | { startedBy: string } | { application: string };

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
  if ("application" in of)
    return lease.application_id === of.application ? lease : null;
  // browserd's started_at against the api's waiting_person time: right only while both share the host clock.
  const started = Date.parse(lease.started_at);
  const by = Date.parse(of.startedBy);
  return started <= by ? lease : null;
}

const signingIn = (lease: ReturnType<typeof applicationLease>) =>
  lease?.state === "waiting_person" && lease.wait_reason === "signin";

function useApplicationLease(
  leaseOf: LeaseOf,
  purposes: ReadonlySet<string> = FILL_PURPOSES,
) {
  const query = useBrowser({
    refetchInterval: LEASE_POLL_MS,
    enabled: leaseOf !== "none",
  });
  return applicationLease(query.data, leaseOf, purposes);
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
  const lease = useApplicationLease(
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
    if (signingIn(lease)) {
      return <HandoffBanner liveOpen />;
    }
    // A wait on the page (a captcha, an e-mail code, a 403 page): browserd gave the person the
    // input for it, so the view is theirs, without Take over; it is the assistant's again after.
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
    <Empty
      data-testid="live-view-slot"
      className="min-h-40 flex-none gap-2 border p-6 md:p-6"
    >
      {stopped ? null : (
        <EmptyMedia className="mb-0 text-muted-foreground">
          <Spinner aria-hidden="true" role={undefined} className="size-5" />
        </EmptyMedia>
      )}
      <EmptyDescription>{idle}</EmptyDescription>
    </Empty>
  );
}

/**
 * The sign in hand off of the application that fills, on its queue card (browser B12b's banner,
 * the live view closed until the person opens it): only while the person's own fill lease waits
 * for them to sign in. Nothing otherwise.
 */
export function FillHandoff() {
  const lease = useApplicationLease("any");
  return signingIn(lease) ? <HandoffBanner /> : null;
}
