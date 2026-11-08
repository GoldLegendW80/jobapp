/**
 * The words of a follow up (SPEC-apply.md, "Follow ups"): one line of where its cadence stands,
 * the line of its dates, and the address that opens it in the person's own mail program. Facts
 * only, no verdict; no dashes as separators.
 */
import { formatDate } from "@/lib/format";

import type { FollowUp } from "../api/followups-api";

/** A mailto address longer than this is cut by some mail programs: the text then goes by Copy. */
export const MAILTO_MAX = 2000;
/** Never more than two follow ups for a silent application (api/app/apply/followups.py). */
export const SILENT_MAX = 2;

function since(next: string | null, today: string): string {
  if (next === null) return "";
  return next === today ? "today" : `since ${formatDate(next)}`;
}

/** Where the posting's follow ups stand, in one sentence. */
export function cadenceLine(item: FollowUp, today: string): string {
  const next = item.next_on;
  if (item.kind === "thank_you") {
    if (item.urgency === "done") return "Thank you note sent.";
    return item.urgency === "due"
      ? `A thank you note for the interview is due ${since(next, today)}.`
      : `A thank you note for the interview is due on ${formatDate(next)}.`;
  }
  switch (item.urgency) {
    case "due":
      return `${item.count === 0 ? "First" : "Second"} follow up due ${since(next, today)}.`;
    case "waiting":
      return `${item.count === 0 ? "First" : "Second"} follow up due on ${formatDate(next)}.`;
    case "cold":
      return "No answer after two follow ups. The app suggests no third one.";
    case "answered":
      return "The company wrote back. Their mail waits for your reply.";
    default:
      return "";
  }
}

/** "Applied on 3 Nov 2026. Follow ups sent: 1 of 2." */
export function datesLine(item: FollowUp): string {
  const applied = `Applied on ${formatDate(item.applied_on)}.`;
  if (item.kind === "thank_you") return applied;
  return `${applied} Follow ups sent: ${item.count} of ${SILENT_MAX}.`;
}

/**
 * The address that opens the draft in the person's own mail program, its text cut when the
 * whole address would pass MAILTO_MAX characters (`cut`: Copy holds the whole text).
 */
export function mailtoHref(
  subject: string,
  body: string,
): { href: string; cut: boolean } {
  const head = `mailto:?subject=${encodeURIComponent(subject)}&body=`;
  const whole = head + encodeURIComponent(body);
  if (whole.length <= MAILTO_MAX) return { href: whole, cut: false };
  // By code points: a cut inside a surrogate pair would make encodeURIComponent throw.
  const chars = Array.from(body);
  let end = chars.length;
  const at = (count: number) =>
    head + encodeURIComponent(chars.slice(0, count).join(""));
  while (end > 0 && at(end).length > MAILTO_MAX) end = Math.floor(end * 0.9);
  return { href: at(end), cut: true };
}
