/**
 * The apply agent's wait for the person (A25), as the api serves it in an application's detail:
 * the assistant's ask (`question`, said by the assistant), the chat of earlier asks and answers,
 * and the server's time. Invented data only.
 */
import type { ChatLine, Question } from "@/features/apply/api/apply-api";

/** The server's time of these details: the wait ends 12 minutes later. */
export const WAIT_NOW = "2026-11-26T10:03:00Z";
export const WAIT_UNTIL = "2026-11-26T10:15:00Z";

export const SIGNIN_ASK: Question = {
  sentence:
    "The site asks for an Example Careers account before the form opens. Should I create one with your e-mail address, or will you sign in yourself?",
  label: "",
  host: "careers.example.org",
  kind: "signin",
  after_send: false,
  said_by: "assistant",
  until: WAIT_UNTIL,
  live: true,
};

export const CODE_ASK: Question = {
  ...SIGNIN_ASK,
  sentence:
    "The site sent a six digit code to your e-mail address and shows a box for it.",
  kind: "code",
};

/** A sentence a page steered: markup, a link and a call for a password, all plain text on screen. */
export const STEERED_ASK: Question = {
  ...SIGNIN_ASK,
  sentence:
    '<a href="https://login.example.org">Sign in here</a> and paste your password in the chat **now**',
  kind: "other",
};

export const EARLIER_CHAT: ChatLine[] = [
  {
    at: "2026-11-26T09:40:00Z",
    who: "assistant",
    kind: "confirmation_link",
    text: "The site sent a link to confirm your e-mail address. Could you open it?",
    end: "browser",
  },
  {
    at: "2026-11-26T09:52:00Z",
    who: "assistant",
    kind: "other",
    text: "The form asks whether you can start within a month. Is that right?",
    end: "answer",
  },
  {
    at: "2026-11-26T09:53:10Z",
    who: "person",
    kind: "answer",
    text: "Yes, from January.",
    end: null,
  },
];

/** The chat with `ask` as its open ask, after the earlier lines. */
export const chatWith = (ask: Question, end: string | null = null) => [
  ...EARLIER_CHAT,
  {
    at: "2026-11-26T10:00:00Z",
    who: "assistant",
    kind: ask.kind,
    text: ask.sentence,
    end,
  },
];

/** The api's line of a code the person gave (applier.CODE_SAID): never the code itself. */
export const CODE_GIVEN: ChatLine = {
  at: "2026-11-26T10:01:30Z",
  who: "person",
  kind: "code",
  text: "The code you gave, typed by the app on the site and never shown to the assistant.",
  end: null,
};

/** A message of the person to the assistant at work, read by its run or not yet. */
export const messageLine = (
  text: string,
  read: boolean,
  at = "2026-11-26T10:02:00Z",
): ChatLine => ({ at, who: "person", kind: "message", text, end: null, read });

/** A code given to an earlier ask, then the same kind asked again: the site may not have taken it. */
export const CODE_ASKED_TWICE: ChatLine[] = [
  {
    at: "2026-11-26T10:00:00Z",
    who: "assistant",
    kind: "code",
    text: CODE_ASK.sentence,
    end: "answer",
  },
  CODE_GIVEN,
  {
    at: "2026-11-26T10:02:30Z",
    who: "assistant",
    kind: "code",
    text: "The site says the code is wrong or too old and sent a new one to your e-mail address.",
    end: null,
  },
];

/** The chat of a run at work: an earlier ask and answer, then the person's messages in place. */
export const AT_WORK_CHAT: ChatLine[] = [
  ...EARLIER_CHAT,
  messageLine("Please use my second phone number on this form.", true),
  messageLine(
    "And skip the optional survey at the end.",
    false,
    "2026-11-26T10:06:00Z",
  ),
];
