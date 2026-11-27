/**
 * The apply agent's wait for the person (A25; SPEC-apply, "The apply agent waits for the person"),
 * as pure functions: whether an application waits or waited, what the assistant asks for in plain
 * words, whether the box takes an answer, and the words of each end. The assistant's sentence and
 * the chat are the model's own words, which a page can steer: they are shown as plain text,
 * bounded (`shownText`), never as a link or markup, and never put inside these sentences. No
 * dashes as separators.
 */
import type {
  Application,
  ApplicationEvent,
  ChatLine,
  Question,
} from "../api/apply-api";

type Run = Pick<Application, "state" | "needs" | "error_code">;

/** The run waits for the person now (api applier.waiting): `needs_person`, or `submitting` after a send button. */
export const agentWaits = (run: Run | null | undefined) =>
  run?.needs === "agent_waits" &&
  (run.state === "needs_person" || run.state === "submitting");

/** The wait ended without the person (api applier.ASKED): the ask stays, with Try again. */
export const agentAsked = (run: Run | null | undefined) =>
  run?.state === "needs_person" && run.needs === "agent_asked";

/** Nobody came in time after a send button had been clicked: A19's `submit_unknown`, the ask kept. */
export const lateAfterSend = (run: Run | null | undefined) =>
  run?.state === "submit_unknown" && run.error_code === "person_late";

/** The application shows the assistant's ask and the chat. */
export const showsAsk = (run: Run | null | undefined) =>
  agentWaits(run) || agentAsked(run) || lateAfterSend(run);

/** The apply agent's ask (A25), not One click Apply's fixed question of A19. */
export const isAsk = (question: Question | null | undefined) =>
  question?.said_by === "assistant";

const KINDS: Record<string, string> = {
  signin: "a sign in",
  account: "an account",
  code: "a code",
  captcha: "a captcha",
  confirmation_link: "a confirmation link",
  other: "something only you can do",
};

/** What the assistant asks for, in plain words; a kind this page does not know reads as `other`. */
export const kindWords = (kind: string) =>
  Object.hasOwn(KINDS, kind) ? KINDS[kind] : KINDS.other;

/**
 * The kinds the app's box refuses (api applier.CODE_IN_BROWSER): the person types them in the
 * browser. The api's next slice lets the app type a code given here: remove it from this set then.
 */
export const TYPED_IN_BROWSER: ReadonlySet<string> = new Set(["code"]);

export const CODE_IN_BROWSER =
  "Type the code in the browser yourself: open the browser below, choose Use the browser myself, type it, then choose Let the assistant continue.";

/** The person's answer to the ask is in, and the run has not read it yet: no second box. */
export function answered(chat: readonly ChatLine[] | undefined): boolean {
  const last = chat?.at(-1);
  return last?.who === "person" && !last.end;
}

/** What the box offers while the run waits: an answer, the browser for a code, or nothing more to say. */
export function boxOf(
  question: Question,
  chat: readonly ChatLine[] | undefined,
): "answer" | "browser" | "sent" | null {
  if (question.live !== true) return null;
  if (answered(chat)) return "sent";
  return TYPED_IN_BROWSER.has(question.kind) ? "browser" : "answer";
}

/** At most this many characters of one line of the chat on screen (the api keeps an ask to 300). */
export const SHOWN_MAX = 600;

// Control and direction characters: a page could make the assistant's words read in another order.
const HIDDEN = /[\u0000-\u001f\u007f-\u009f​‎‏‪-‮⁦-⁩﻿]/g;

/** A line of the chat as shown: plain text on one line, cut at `max` characters with an ellipsis. */
export function shownText(text: string, max = SHOWN_MAX): string {
  const flat = text.replace(HIDDEN, " ").replace(/\s+/g, " ").trim();
  const characters = Array.from(flat);
  return characters.length > max
    ? `${characters.slice(0, max).join("").trimEnd()}…`
    : flat;
}

/** How each ask ended (api applier: the ask's `end`), said under it in the chat. */
const ASK_ENDS: Record<string, string> = {
  answer: "You answered here.",
  browser: "You used the browser, then let the assistant continue.",
  person_late: "Nobody answered in time.",
  run_ended: "The assistant's run ended while it waited.",
  interrupted: "The app was interrupted while the assistant waited.",
  cancelled: "You cancelled the application.",
};

export const askEndLine = (end: string | null | undefined) =>
  end && Object.hasOwn(ASK_ENDS, end) ? ASK_ENDS[end] : null;

/**
 * Why the wait ended without the person, from the last ask's end: nobody came in time, the run
 * or its browser ended, or the app was interrupted. The application then waits for Try again.
 */
export function stoppedLine(chat: readonly ChatLine[] | undefined): string {
  const end = (chat ?? [])
    .filter((line) => line.who === "assistant")
    .at(-1)?.end;
  if (end === "run_ended")
    return "The assistant's run ended while it waited for you (its time limit, or the browser closed).";
  if (end === "interrupted")
    return "The app was interrupted while the assistant waited for you.";
  return "Nobody answered the assistant in time, so it stopped and gave the browser back.";
}

export const AFTER_SEND_WAIT =
  "A send button had been clicked before it asked, so the site may already have the application.";

export const NO_PASSWORD =
  "The assistant never needs your password. Type passwords only in the browser yourself.";

/** The answer came after the wait ended (409): it was not given to the assistant. */
export const LATE_ANSWER =
  "Your answer came after the assistant stopped waiting, so it did not get it.";

/**
 * What the assistant said with `apply_done` when its run ended (api applier, the `agent_done`
 * event): yes, no or unsure was a send button clicked, `person` for the person's own send.
 */
export function saidSent(
  events: readonly ApplicationEvent[] | undefined,
): string | null {
  const done = (events ?? [])
    .filter((event) => event.kind === "agent_done")
    .at(-1);
  const sent = done?.data.sent;
  return typeof sent === "string" ? sent : null;
}

/** The app could not confirm it, and the assistant says nothing was sent: It was sent is not first. */
export const saidNothingSent = (
  run: Run | null | undefined,
  events: readonly ApplicationEvent[] | undefined,
) => run?.state === "submit_unknown" && saidSent(events) === "no";
