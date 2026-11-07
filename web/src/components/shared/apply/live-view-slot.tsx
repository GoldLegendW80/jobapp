import { Empty, EmptyDescription, EmptyMedia } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { askText } from "@/features/agents/helpers/run-browser";
import {
  leasesOf,
  personOwnsInput,
  type Browser,
} from "@/features/browser/api/browser-api";
import { useBrowser } from "@/features/browser/api/use-browser";
import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { HandoffBanner } from "@/components/shared/browser/handoff-banner";
import { LiveViewPlaceholder } from "@/components/shared/browser/live-views";

// How often the slot reads whether the assistant holds the browser (it ends, or starts).
const LEASE_POLL_MS = 3_000;

/**
 * The purposes of an application's fill: api run.PURPOSE and run.FULL_AUTO_PURPOSE, and the apply
 * agent's of One click Apply and Review first (applier.PURPOSE, applier.REVIEW_PURPOSE).
 */
const FILL_PURPOSES = new Set([
  "apply_fill",
  "full_auto",
  "apply_agent",
  "apply_review",
]);
/** The purpose of the submit lease (api submit.PURPOSE). */
const SUBMIT_PURPOSES = new Set(["apply_submit"]);

/**
 * Which of the person's leases is this application's: `any` fill lease (the application fills,
 * and a person has one fill at a time), one that started by `startedBy` (a later one is the fill
 * of another posting), the one the api names for `application` (the submit lease), or `none`.
 */
export type LeaseOf =
  "any" | "none" | { startedBy: string } | { application: string };

/**
 * The lease of this application among the person's (one per window of their browser), the one
 * the live view shows; null otherwise.
 */
function applicationLease(
  browser: Browser | undefined,
  of: LeaseOf,
  purposes: ReadonlySet<string>,
) {
  if (of === "none") return null;
  const leases = leasesOf(browser).filter(
    (lease) =>
      lease.automation &&
      lease.state !== "starting" &&
      purposes.has(lease.purpose),
  );
  if (of === "any") return leases[0] ?? null;
  if ("application" in of)
    return (
      leases.find((lease) => lease.application_id === of.application) ?? null
    );
  // browserd's started_at against the api's waiting_person time: right only while both share the host clock.
  const by = Date.parse(of.startedBy);
  return leases.find((lease) => Date.parse(lease.started_at) <= by) ?? null;
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
 * the submit lease's slot instead, watch only. `label` (and `detail`): the view of a grid of
 * views (LiveViews, the Applications page), named and with its state; a sign in hand off then
 * stays on the application's card (FillHandoff).
 */
export function LiveViewSlot({
  idle,
  leaseOf,
  status = "The assistant is filling the form.",
  stopped = false,
  submit = false,
  label,
  detail,
}: {
  idle: string;
  leaseOf: LeaseOf;
  status?: string;
  stopped?: boolean;
  submit?: boolean;
  label?: string;
  detail?: string;
}) {
  const lease = useApplicationLease(
    leaseOf,
    submit ? SUBMIT_PURPOSES : FILL_PURPOSES,
  );
  if (lease && submit) {
    return (
      <div data-testid="live-view-slot">
        <LiveBrowser
          key={lease.id}
          slot={lease.slot ?? 0}
          label={label}
          detail={detail}
          watchOnly
          status={status}
        />
      </div>
    );
  }
  if (lease) {
    if (signingIn(lease)) {
      return label ? (
        <div data-testid="live-view-slot">
          <LiveViewPlaceholder label={label} detail={detail} state="needs-you">
            {`The assistant waits for you to sign in to ${lease.wait_host ?? "the website"}. Sign in from this application's card.`}
          </LiveViewPlaceholder>
        </div>
      ) : (
        <HandoffBanner liveOpen />
      );
    }
    // A wait on the page (a captcha, an e-mail code, a 403 page): browserd gave the person the
    // input for it, so the view is theirs, without Take over; it is the assistant's again after.
    const person = personOwnsInput(lease);
    const ask = askText(lease);
    return (
      <div data-testid="live-view-slot">
        <LiveBrowser
          key={`${lease.id}:${person}`}
          slot={lease.slot ?? 0}
          label={label}
          detail={detail}
          person={person}
          needsYou={person || ask !== null}
          status={
            person
              ? "You have the keyboard and the mouse. Complete this step in the live view."
              : (ask ?? status)
          }
        />
      </div>
    );
  }
  if (label) {
    return (
      <div data-testid="live-view-slot">
        <LiveViewPlaceholder
          label={label}
          detail={detail}
          state={stopped ? "ended" : "waiting"}
        >
          {idle}
        </LiveViewPlaceholder>
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
