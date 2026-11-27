import type {
  Application,
  ApplicationDetail,
  Question,
} from "../api/apply-api";
import { agentAsked, agentWaits } from "./agent-wait";
import type { DecisionKind } from "./decisions";

/** Where the person settles what the documents of an application wait for. */
const DOCUMENTS_WAIT_LINKS = new Map([
  ["documents:credential", { to: "/account", label: "Open your account" }],
]);

/** The link of a queued application waiting for its documents, else null. */
export function documentsWaitLink(
  application: Application | null,
): { to: string; label: string } | null {
  if (application?.state !== "queued" || !application.needs) return null;
  return DOCUMENTS_WAIT_LINKS.get(application.needs) ?? null;
}

/** Every state but an end, where the api takes a Cancel (api/app/apply/gate.py, CANCELLABLE). */
const CANCELLABLE: ReadonlySet<string> = new Set([
  "queued",
  "filling",
  "needs_person",
  "ready",
  "approved",
  "held",
  "refused_by_site",
  "submitting",
]);

export type CancelKind = "cancel" | "cancelAfterSend" | "notSent";

/**
 * The Cancel an application offers at any moment (owner, 2026-11-26), in every mode but Apply
 * myself, which keeps its Close: Cancel before any act on a send button, the same Cancel saying
 * the site may have it once the assistant acted on one (`submitting`), and "It was not sent" on
 * an application the app could not confirm. An e-mail application being sent keeps the mail's.
 */
export function cancelKindOf(
  application: Application | null | undefined,
  afterSend = false,
): CancelKind | null {
  if (!application || application.mode === "manual") return null;
  const email = application.ats === "email";
  if (application.state === "submit_unknown") return email ? null : "notSent";
  if (application.state === "submitting")
    return email ? null : "cancelAfterSend";
  if (!CANCELLABLE.has(application.state)) return null;
  // A25: the assistant asked after a send button (a step Review first traced): it may be sent.
  return afterSend && !email ? "cancelAfterSend" : "cancel";
}

/**
 * This application's submit lease among the person's leases: the one the api names for it, as the
 * person may have several applications approved or held; null in a state without one, and while
 * the apply agent waits for the person (its card shows that browser behind Watch the browser).
 */
export function submitLeaseOf(
  application: Application | null,
): { application: string } | null {
  if (!application || application.ats === "email" || agentWaits(application))
    return null;
  return ["approved", "held", "submitting"].includes(application.state)
    ? { application: application.id }
    : null;
}

/** Try again after a click the app could not confirm: the run that tries again is the first one's. */
export function retryKindOf(
  detail: ApplicationDetail | undefined,
): DecisionKind {
  if (detail?.review_agent) return "retryReview";
  return detail?.agent ? "retryAgent" : "retry";
}

/**
 * Try again after the assistant stopped waiting for the person (A25, `agent_asked`): one more run
 * with the earlier asks and answers, saying so when a send button had been clicked before.
 */
export function askRetryOf(
  application: Application | null | undefined,
  question: Question | null | undefined,
): "retryAsked" | "retryAskedAfterSend" | null {
  if (!agentAsked(application)) return null;
  return question?.after_send ? "retryAskedAfterSend" : "retryAsked";
}
