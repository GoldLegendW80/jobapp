import type { Application, ApplicationDetail } from "../api/apply-api";
import { decidedByApp } from "./apply-state";
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

/**
 * Cancel is offered where the api accepts it and no click happened yet, a Full auto or one click
 * Apply item the app still decides on included (a `ready` one in Review first has Reject instead).
 */
export function canCancelApplication(application: Application | null): boolean {
  if (!application) return false;
  if (application.state === "ready") return decidedByApp(application);
  return ["queued", "filling", "needs_person", "approved", "held"].includes(
    application.state,
  );
}

/**
 * This application's submit lease among the person's leases: the one the api names for it, as the
 * person may have several applications approved or held; null in a state without one.
 */
export function submitLeaseOf(
  application: Application | null,
): { application: string } | null {
  if (!application || application.ats === "email") return null;
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
