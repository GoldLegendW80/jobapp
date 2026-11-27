/**
 * What the live view of an application says (A23): over the window of its lease, the status line
 * for that lease's purpose and the application's state; while it has no lease, what it waits for,
 * read from its own `needs` (api run._wait). No dashes as separators.
 */
import type { Application } from "../api/apply-api";

type Run = Pick<Application, "mode" | "state" | "dry_run" | "needs">;

/** The submit lease (api submit.PURPOSE): the person only watches it. */
export const SUBMIT_PURPOSE = "apply_submit";
/** Review first's apply agent (api applier.REVIEW_PURPOSE). */
const REVIEW_PURPOSE = "apply_review";

const STATUS = {
  fill: "The assistant is filling the form.",
  agentFills:
    "The assistant is filling the form. It sends it by itself once it is filled.",
  // `submitting` starts at the agent's first act on a submit element (api applier.arm).
  agentSends:
    "The assistant is sending the form. It may still fill a step or two first.",
  reviewFills:
    "The assistant is filling the form. Then you check what it filled and confirm.",
  reviewSends:
    "The app sends the form you confirmed. The assistant then reads the site's answer.",
  submits:
    "The assistant fills the form again with your approved answers, then submits it.",
  rehearses:
    "A rehearsal: the assistant fills the form again with your approved answers, then stops without submitting it.",
  // One click Apply sent on the submit lease (mode `direct` without the apply agent).
  directSubmits:
    "The assistant fills the form again with the same answers, then sends it.",
  // A25: the apply agent asked the person and waits (api applier.WAITS).
  agentWaits:
    "The assistant waits for you. Choose Use the browser myself to do what it asks, then Let the assistant continue.",
};

/**
 * The status line over the live view of the lease the api names for `run`: chosen by the lease's
 * purpose, then by what the run does now (filling the form, then sending it).
 */
export function liveStatus(purpose: string, run: Run | null): string {
  const filling = run?.state === "filling";
  if (run?.needs === "agent_waits" && purpose !== SUBMIT_PURPOSE)
    return STATUS.agentWaits;
  switch (purpose) {
    case SUBMIT_PURPOSE:
      if (run?.dry_run) return STATUS.rehearses;
      return run?.mode === "direct" ? STATUS.directSubmits : STATUS.submits;
    case "apply_agent":
      return filling ? STATUS.agentFills : STATUS.agentSends;
    case REVIEW_PURPOSE:
      return filling ? STATUS.reviewFills : STATUS.reviewSends;
    default:
      return STATUS.fill;
  }
}

/**
 * The line under a live view the person took over (A25): the browser is theirs until they give it
 * back, for the minutes the api serves for the take over's bound (browserd TAKEOVER_S); null for a
 * lease not taken over.
 */
export function takeoverLine(lease: {
  wait_reason: string | null;
  wait_minutes_left?: number | null;
}): string | null {
  if (lease.wait_reason !== "takeover") return null;
  const minutes = lease.wait_minutes_left;
  if (typeof minutes !== "number")
    return "The browser is yours until you choose Let the assistant continue.";
  const left =
    minutes <= 1 ? "1 more minute" : `${String(minutes)} more minutes`;
  return `The browser is yours until you choose Let the assistant continue, for ${left} at most: then the assistant has it back.`;
}

/**
 * Whether the person only watches the lease the api names for `run`, without Use the browser
 * myself: the submit lease, and Review first's agent past its fill. After the person's Confirm
 * (`approved`, then `submitting`) the gate clicks in that held session on the page it checked
 * once (api confirm.hold), so the submit stays watch only. One click Apply's agent keeps it.
 */
export function liveWatchOnly(
  purpose: string,
  run: Pick<Run, "state"> | null,
): boolean {
  if (purpose === SUBMIT_PURPOSE) return true;
  return purpose === REVIEW_PURPOSE && run?.state !== "filling";
}

/** What an application waits for, from the `needs` the api wrote while it waits. */
const WAITS = new Map([
  [
    "host_pace",
    "Waiting for its turn. The app spaces out submissions to the same site, so this one waits a little.",
  ],
  [
    "browser_queue",
    "Waiting for the browser, which is busy with another task. The live view shows here once it is free.",
  ],
  [
    "browser_unavailable",
    "The browser cannot be reached just now. The app tries again by itself.",
  ],
  [
    "browser_signin",
    "Waiting for you to finish signing in to a site in your browser. The form is filled after that.",
  ],
  [
    "retry",
    "The last attempt did not finish. The assistant tries again by itself.",
  ],
]);
const DOCUMENTS = "The browser starts once your CV and letter are ready.";

/** What a run without its lease waits for when `needs` names nothing, by its state. */
const QUEUED =
  "The browser starts soon. Then you can watch the assistant fill the form here.";
const BY_STATE = new Map([
  ["queued", QUEUED],
  ["filling", "Your browser is starting."],
  [
    "approved",
    "Your browser starts soon. Then you can watch the assistant send the form here.",
  ],
  [
    "held",
    "After the hold, you can watch the assistant fill the form again here.",
  ],
  [
    "submitting",
    "Sending the form. Its result shows here once the app has it.",
  ],
]);

/**
 * The sentence in the place of the live view while the application has no lease: what it waits
 * for, from its own `needs` first, then its state. Only `host_pace` (api submit.prepare and arm,
 * the pace of A11) says that submissions are spaced out.
 */
export function liveWait(run: Run): string {
  const needs = run.needs ?? "";
  const waits = WAITS.get(needs);
  if (waits) return waits;
  if (needs.startsWith("documents:")) return DOCUMENTS;
  return BY_STATE.get(run.state) ?? QUEUED;
}
