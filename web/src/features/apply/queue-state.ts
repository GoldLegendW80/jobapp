/**
 * What a card of the review queue shows, as pure functions: which actions an item offers, the
 * line of each document with its check, what is left for the person, and how often the page
 * reads the queue again. The api's own sentences (a reason, a refusal) are shown as they come,
 * and a reason without one gets a label here.
 * No dashes as separators.
 */
import { documentLine } from "./apply-state";
import type { DocumentCheck, Queue, QueueItem } from "./queue-api";

export const QUEUE_POLL_MS = 3000;
/** Pace expiry is not exposed by the api; keep following it without rapid polling. */
export const QUEUE_WAIT_POLL_MS = 30_000;
/** The shell's count follows the queue page at once; elsewhere it is read again this often. */
export const COUNT_POLL_MS = 60_000;
const WAITING = new Set([
  "ready",
  "needs_person",
  "refused_by_site",
  "submit_unknown",
]);

/** The item waits for the person (a Full auto rehearsal waits for its answer); the api lists these first. */
export const isWaiting = (item: QueueItem) =>
  (WAITING.has(item.state) && !isDeciding(item)) ||
  item.rehearsal === true ||
  (item.mode === "manual" && item.needs === "session_ended");

/** A Full auto item filled and `ready` for the app's own decision: under way, not the person's. */
export const isDeciding = (item: QueueItem) =>
  item.state === "ready" && item.mode === "full_auto";

/** A Full auto item in its hold: the countdown shows it, with its own Cancel. */
export const isHeld = (item: QueueItem) =>
  item.state === "held" && Boolean(item.hold_until);

/** Wait for known holds on the server's clock, then follow the worker's next transition. */
export function queuePoll(
  queue: Queue | undefined,
  pageNow: number,
  offset = 0,
): number | false {
  let next = Number.POSITIVE_INFINITY;
  for (const item of queue?.items ?? []) {
    if (isWaiting(item)) continue;
    let interval = QUEUE_POLL_MS;
    if (item.state === "held") {
      const end = Date.parse(item.hold_until ?? "");
      const left = end - (pageNow + offset);
      const afterHold =
        item.needs === "host_pace" ? QUEUE_WAIT_POLL_MS : QUEUE_POLL_MS;
      interval = !Number.isFinite(end)
        ? QUEUE_WAIT_POLL_MS
        : left > 0
          ? left
          : afterHold;
    } else if (item.state === "approved" && item.needs === "host_pace") {
      interval = QUEUE_WAIT_POLL_MS;
    }
    next = Math.min(next, interval);
  }
  return Number.isFinite(next) ? next : false;
}

/** The form is filled and its answers can be read and corrected. */
export const isReviewable = (item: QueueItem) =>
  (item.state === "ready" && !isDeciding(item)) ||
  item.state === "needs_person";

/** Reject is the api's for a filled form not yet approved. */
export const canReject = isReviewable;

/** Cancel stops an item under way before any click (a held one has the countdown's Cancel). */
export const canCancel = (item: QueueItem) =>
  item.mode !== "manual" &&
  (["queued", "filling", "approved", "held"].includes(item.state) ||
    isDeciding(item)) &&
  !isHeld(item);

/** What only the posting's page can do: the live view, "It was submitted", "Try again". */
export const needsPostingPage = (item: QueueItem) =>
  item.mode === "manual" ||
  ["needs_person", "refused_by_site", "submit_unknown"].includes(item.state);

const FITS: Record<string, string> = {
  one_page: "fits on one page",
  overflow: "runs over one page",
};

/** A document needs a look: it runs over its page, its page is not checked, or it has corrections. */
export const needsLook = (check: DocumentCheck) =>
  check.fit !== "one_page" || check.corrections > 0;

/** "CV version 3: fits on one page", "Letter version 2: runs over one page, 1 correction to look at". */
export function checkLine(kind: string, check: DocumentCheck): string {
  const parts = [FITS[check.fit ?? ""] ?? "page not checked yet"];
  if (check.corrections > 0) {
    parts.push(
      check.corrections === 1
        ? "1 correction to look at"
        : `${check.corrections} corrections to look at`,
    );
  }
  return `${documentLine(kind, check)}: ${parts.join(", ")}`;
}

/** "2 fields left for you, 1 of them required", or null when nothing is left. */
export function leftLine(item: QueueItem): string | null {
  if (item.left <= 0) return null;
  if (item.left === 1) {
    return item.required_left > 0
      ? "1 field left for you, required."
      : "1 field left for you, optional.";
  }
  const required =
    item.required_left > 0
      ? `, ${item.required_left} of them required`
      : ", all optional";
  return `${item.left} fields left for you${required}.`;
}

/**
 * The reasons the api sends without a sentence: what the fill or the submit lease stopped on
 * before any click (api/app/apply/run.py and submit.py), and `other`, the summary's reason of an
 * item sent to the person without a code.
 */
const REASONS: Record<string, string> = {
  session_ended:
    "Your browser session ended. Reopen it, record your application or close it.",
  captcha: "The site asked for a captcha, so it waits for you.",
  signin: "The site asked you to sign in, so it waits for you.",
  email_code:
    "The site sent a code by e-mail that only you can read, so it waits for you.",
  required_left: "A required question was left for you to answer.",
  payment_requested: "The site asked for a payment, so it waits for you.",
  no_adapter: "The app does not know this form yet, so it waits for you.",
  needs_person: "The form needed you before it could go on.",
  form_changed: "The form changed since it was filled, so it waits for you.",
  no_documents:
    "The CV or the letter of this form could not be read, so it waits for you.",
  no_capability:
    "The browser could not be made ready for the click, so it waits for you.",
  put_file_failed:
    "A file could not be attached to the form, so it waits for you.",
  failed: "The form could not be opened, so it waits for you.",
  ended: "The browser session ended before the click, so it waits for you.",
  host_not_allowed: "The form moved to another site, so it waits for you.",
  unreadable: "The page could not be read, so it waits for you.",
  other: "Another reason: open it to see where it stopped.",
};

/** A code this page does not know yet: said in words, never shown raw. */
export const OTHER_REASON =
  "The app stopped before the click, so it waits for you.";

/** Why a filled item came back to the person: the api's sentence, else a label of its code. */
export function reasonLine(reason: QueueItem["reason"]): string | null {
  if (!reason) return null;
  if (reason.sentence) return reason.sentence;
  const label = Object.hasOwn(REASONS, reason.code)
    ? REASONS[reason.code]
    : undefined;
  return label ?? OTHER_REASON;
}
