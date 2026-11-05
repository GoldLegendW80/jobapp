/**
 * The decisions on an application that need a second click, each with its own sentence and the
 * call it makes. The posting's Apply section and the queue's cards ask with these same words and
 * make these same calls. No dashes as separators.
 */
import {
  cancelApplication,
  confirmSubmitted,
  rejectApplication,
  retryApplication,
  type Application,
} from "./apply-api";
import type { Confirmation } from "./confirm-dialog";

export const DECISIONS = {
  reject: {
    title: "Reject this filled form?",
    sentence: "Nothing is sent. You can start again.",
    confirm: "Reject the form",
    call: rejectApplication,
  },
  cancel: {
    title: "Cancel this application?",
    sentence: "Nothing is sent.",
    confirm: "Cancel the application",
    call: cancelApplication,
  },
  submitted: {
    title: "Mark this application as submitted?",
    sentence:
      "Only do this if you saw the confirmation. It cannot be undone, and the app will refuse another application for this posting.",
    confirm: "Mark as submitted",
    call: confirmSubmitted,
  },
  retry: {
    title: "Fill and approve again?",
    sentence:
      "If the first one went through, the company may receive two applications.",
    confirm: "Fill and approve again",
    call: retryApplication,
  },
} satisfies Record<
  string,
  Confirmation & { call: (applicationId: string) => Promise<Application> }
>;
export type DecisionKind = keyof typeof DECISIONS;
