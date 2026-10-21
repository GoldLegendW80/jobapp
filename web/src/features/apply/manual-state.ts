/**
 * What Apply myself's panel says (apply A9), as pure functions of the session's status. No dashes
 * as separators.
 */
import type { ManualStatus } from "./manual-api";

/** While the session waits for the browser, the panel asks for it again this often. */
export const REOPEN_MS = 10_000;

const DOCUMENT: Record<string, string> = {
  attach_cv: "CV",
  attach_letter: "letter",
};

/** What the last action of the panel came to, in the person's words. */
export function outcomeLine(last: ManualStatus["last"]): string | null {
  if (!last) return null;
  const [code, count] = last.outcome.split(":");
  const n = Number(count ?? 0);
  if (last.action === "fill_details") {
    if (code === "filled")
      return n === 0
        ? "No empty field asked for your details."
        : `Filled ${n} field${n === 1 ? "" : "s"} with your details. Check them before you send.`;
    if (code === "taken")
      return `You took the keyboard back, so the app stopped after ${n} field${n === 1 ? "" : "s"}.`;
    return "Your details could not be filled. You can type them yourself.";
  }
  const document = DOCUMENT[last.action];
  if (!document) return null;
  if (code === "attached") return `Your ${document} is attached to the form.`;
  if (code === "pick")
    return `The app could not tell which field takes your ${document}. Click that field's button in the form, then click Attach my ${document} again.`;
  if (code === "no_document")
    return `No ${document} was placed in your browser for this posting.`;
  return `Your ${document} could not be attached. You can try again.`;
}

export function busyLine(busy: string | null | undefined): string | null {
  if (busy === "fill_details")
    return "The app is filling your details. Your keys reach the page again when it is done.";
  if (busy && DOCUMENT[busy]) return `Attaching your ${DOCUMENT[busy]}.`;
  return null;
}
