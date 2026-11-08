/**
 * What the last check of a posting's own address found, in words (SPEC-jobs, "Liveness"): what
 * the page said or why it could not be read, never a guess of the app's own.
 */
import { formatDate } from "@/lib/format";

import type { Liveness } from "../api/liveness-api";

export const MAY_BE_CLOSED = "May be closed";
export const NOT_CHECKED =
  "Not checked yet. The app reads its page before it writes documents or applies.";

/** The statuses a closed page matters to: not applied yet, not passed. */
const OPEN = new Set(["found", "shortlisted", "cv_ready", "staged"]);

/**
 * The check found that the page may say the posting is closed, and the posting is still open: the
 * person decides. A Discover card has no status: every card is a posting still found.
 */
export function mayBeClosed(job: {
  status?: string;
  liveness?: Liveness | null;
}): boolean {
  const verdict = job.liveness?.verdict;
  const open = job.status === undefined || OPEN.has(job.status);
  return open && (verdict === "may_be_closed" || verdict === "closed");
}

/** Why a page could not be read, by the check's reason. */
const UNREAD: Record<string, string> = {
  refused: "the site refused the request",
  rate_limited: "the site asked to slow down",
  server_error: "the site had an error",
  sign_in: "the site asks to sign in first",
  bot_check: "the site showed a robot check",
  unreadable: "its page shows too little text, or text the check cannot decode",
  not_a_page: "the address is not a web page",
  address_refused: "its address cannot be checked",
  network: "the site did not answer",
  timeout: "the site did not answer in time",
  busy: "the app could not pace its requests to the site just now",
  check_failed: "the check itself failed",
  too_many_redirects: "the address redirects too many times",
  unexpected_answer: "the site gave an unexpected answer",
};

/** One sentence on what the last check found, for the posting's status. */
export function checkWords(liveness: Liveness, status: string): string {
  if (liveness.reason === "linkedin")
    return "LinkedIn postings are not checked: LinkedIn asks to sign in first.";
  if (liveness.reason === "no_address")
    return "This posting has no address to check.";
  const when = `Checked on ${formatDate(liveness.checked_at)}`;
  const gone = liveness.reason === "http_404" || liveness.reason === "http_410";
  switch (liveness.verdict) {
    case "closed": {
      const said = gone
        ? "its page no longer exists"
        : "its page says it is closed";
      return status === "closed" && liveness.moved
        ? `${when}: ${said}, so it moved to Closed.`
        : `${when}: ${said}.`;
    }
    case "may_be_closed":
      return liveness.reason === "redirected"
        ? `${when}: its address now leads to another page.`
        : `${when}: its page may say it is closed.`;
    case "no_closure":
      return `${when}: its page shows no sign of closing.`;
    case "unconfirmed":
      return `${when}: the page could not be read, ${UNREAD[liveness.reason] ?? "for a reason this page does not know"}.`;
    default:
      return `${when}.`;
  }
}
