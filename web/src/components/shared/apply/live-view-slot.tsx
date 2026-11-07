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
 * Which of the person's leases is this application's: the one the api names for `application`
 * (its fill, or its submit lease; two fills of the person can run side by side), or `none`.
 */
export type LeaseOf = "none" | { application: string };

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
  const own = leases.find((lease) => lease.application_id === of.application);
  if (own || purposes === SUBMIT_PURPOSES) return own ?? null;
  // A fill lease taken back after Redis lost it names no application: it is this one's only if it is the person's one fill.
  const [only, ...others] = leases;
  return only && others.length === 0 && !only.application_id ? only : null;
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
 * the live view closed until the person opens it): only while this application's fill lease waits
 * for the person to sign in. Nothing otherwise.
 */
export function FillHandoff({ applicationId }: { applicationId: string }) {
  const lease = useApplicationLease({ application: applicationId });
  return signingIn(lease) ? <HandoffBanner /> : null;
}
