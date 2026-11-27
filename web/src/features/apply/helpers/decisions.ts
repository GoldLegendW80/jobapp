/**
 * The decisions on an application that need a second click, each with its own sentence and the
 * call it makes. The posting's Apply section and the queue's cards ask with these same words and
 * make these same calls. No dashes as separators.
 */
import {
  cancelApplication,
  confirmSubmitted,
  notSentApplication,
  rejectApplication,
  retryApplication,
  type Application,
} from "../api/apply-api";

/** What a destructive click says before it does anything: one sentence per action. */
export type Confirmation = {
  title: string;
  sentence: string;
  confirm: string;
};

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
  // A24: after the assistant's first act on a send button the form may have reached the site.
  cancelAfterSend: {
    title: "Cancel this application?",
    sentence:
      "A send button was already clicked, so the site may have it. Nothing more is sent. Check the site or your mail afterwards.",
    confirm: "Cancel the application",
    call: cancelApplication,
  },
  notSent: {
    title: "Mark this application as not sent?",
    sentence:
      "Only do this if you checked the site or your mail and found nothing. The application ends, the app never sends it by itself, and you can apply to this posting again.",
    confirm: "It was not sent",
    call: notSentApplication,
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
  // One click Apply's agent (A19): Try again is one more run of the agent, which sends.
  retryAgent: {
    title: "Apply again?",
    sentence:
      "The assistant applies again and sends it by itself. It looks on the site first for the earlier one, but if that one went through, the company may receive two applications.",
    confirm: "Apply again",
    call: retryApplication,
  },
  // Review first's agent (A20): Try again fills again and shows a new list; nothing is sent
  // before the person's Confirm.
  retryReview: {
    title: "Fill it again?",
    sentence:
      "The assistant looks on the site first for the earlier one, then fills the form again and shows you a new list to confirm. If the earlier one went through, the company may receive two applications.",
    confirm: "Fill it again",
    call: retryApplication,
  },
  // A25: the assistant stopped waiting for the person before any send button was clicked.
  retryAsked: {
    title: "Try again?",
    sentence:
      "The assistant starts again on the site, with what it asked you and your answers so far. It may ask you again.",
    confirm: "Try again",
    call: retryApplication,
  },
  // A25: the same, after a send button had been clicked before the assistant asked.
  retryAskedAfterSend: {
    title: "Try again?",
    sentence:
      "The assistant starts again on the site, with what it asked you and your answers so far. A send button had been clicked before, so if that went through, the company may receive two applications.",
    confirm: "Try again",
    call: retryApplication,
  },
} satisfies Record<
  string,
  Confirmation & { call: (applicationId: string) => Promise<Application> }
>;
export type DecisionKind = keyof typeof DECISIONS;
