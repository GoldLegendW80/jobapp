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

/** The kind of an ask whose answer is a code: the app types it on the site (api applier.answer_code). */
export const CODE = "code";

/** Under the words box of any other ask: its words go to the model, so no code and no password. */
export const NO_CODE_HERE =
  "Type codes and passwords in the browser yourself, never in this box.";

/** Under the words box of a code question: the code has its own box. */
export const WORDS_NOT_CODE =
  "Words here go to the assistant as you write them: give the code in the code box, never here.";

/** Beside the code box: what becomes of the code. */
export const CODE_TYPED_BY_APP =
  "A code of 6 to 16 letters or digits. The app types it on the site for you, and the assistant never sees it.";

/** The other way, for a shorter code or when the person prefers. */
export const CODE_BY_HAND =
  "For a shorter code, or if you prefer: open the browser, choose Use the browser myself, type the code there, then choose Let the assistant continue.";

/** A code question after a code the person gave in this chat. */
export const CODE_ASKED_AGAIN =
  "You gave a code before, and the assistant asks again: the site may not have taken it. Give the newest code the site sent you.";

/** The person's message to the run at work (A25, api applier.post_message), not an answer to an ask. */
export const isMessage = (line: ChatLine) =>
  line.who === "person" && line.kind === "message";

/** The asks and answers of the chat, the person's messages left out. */
const exchange = (chat: readonly ChatLine[] | undefined) =>
  (chat ?? []).filter((line) => !isMessage(line));

/** The person's answer to the ask is in (words or a code), and the run has not read it yet. */
export function answered(chat: readonly ChatLine[] | undefined): boolean {
  const last = exchange(chat).at(-1);
  return last?.who === "person" && !last.end;
}

/**
 * What the boxes offer while the run waits: words, a code (with the words box beside it), nothing
 * more since words or a code are in, or nothing at all once the wait is over.
 */
export function boxOf(
  question: Question,
  chat: readonly ChatLine[] | undefined,
): "answer" | "code" | "sent" | "code_sent" | null {
  if (question.live !== true) return null;
  if (answered(chat))
    return exchange(chat).at(-1)?.kind === CODE ? "code_sent" : "sent";
  return question.kind === CODE ? "code" : "answer";
}

/** The open ask is a code, and the person gave a code to an earlier ask of this chat. */
export function codeAskedAgain(chat: readonly ChatLine[] | undefined): boolean {
  const lines = exchange(chat);
  const asks = lines.flatMap((line, index) =>
    line.who === "assistant" ? [index] : [],
  );
  const last = asks.at(-1);
  if (last === undefined || lines.at(last)?.kind !== CODE) return false;
  return lines
    .slice(0, last)
    .some((line) => line.who === "person" && line.kind === CODE);
}

type Working = Run & Pick<Application, "mode" | "dry_run" | "ats">;

/**
 * The apply agent works on the application now, so a message reaches it (api applier.under_way):
 * One click Apply, or Review first by the agent (`reviewAgent`), filling, sending or waiting for
 * the person; never a rehearsal or an e-mail application. The api decides: its 409 says so.
 */
export const assistantWorks = (
  run: Working | null | undefined,
  reviewAgent: boolean,
) =>
  !!run &&
  !run.dry_run &&
  run.ats !== "email" &&
  (run.mode === "direct" || (run.mode === "review" && reviewAgent)) &&
  (run.state === "filling" || run.state === "submitting" || agentWaits(run));

/** A message of the person the run has not read yet, as the api says (`read` false). */
export const unread = (chat: readonly ChatLine[] | undefined) =>
  (chat ?? []).some((line) => isMessage(line) && line.read === false);

/** Under a message of the person: whether the assistant read it, only as the api says it. */
export function readLine(line: ChatLine): string | null {
  if (!isMessage(line)) return null;
  if (line.read === true) return "The assistant has read it.";
  if (line.read === false) return "The assistant has not read it yet.";
  return null;
}

/** At most this many characters of a message on screen (the api takes 1,000). */
export const MESSAGE_SHOWN_MAX = 1000;

/** At most this many characters of one line of the chat on screen (the api keeps an ask to 300). */
export const SHOWN_MAX = 600;

// Control and direction characters: a page could make the assistant's words read in another order.
const hidden = (code: number) =>
  code < 0x20 ||
  (code >= 0x7f && code <= 0x9f) ||
  code === 0x061c ||
  code === 0x180e ||
  code === 0x200b ||
  code === 0x200e ||
  code === 0x200f ||
  (code >= 0x202a && code <= 0x202e) ||
  (code >= 0x2060 && code <= 0x2064) ||
  (code >= 0x2066 && code <= 0x2069) ||
  code === 0xfeff;

/** A line of the chat as shown: plain text on one line, cut at `max` characters with an ellipsis. */
export function shownText(text: string, max = SHOWN_MAX): string {
  const flat = Array.from(text, (one) =>
    hidden(one.codePointAt(0) ?? 0) ? " " : one,
  )
    .join("")
    .replace(/\s+/g, " ")
    .trim();
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

/** The run waits, and the detail carries no ask of the assistant's to show. */
export const ASK_NOT_SHOWN =
  "What the assistant asks could not be shown here. Cancel the application, or let it stop waiting by itself.";

export const NO_PASSWORD =
  "The assistant never needs your password. Type passwords only in the browser yourself.";

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

/** An end the app could not confirm, in the words of what the assistant said (api applier). */
const UNCONFIRMED: Record<string, string> = {
  person_late:
    "Nobody answered the assistant in time, after a send button had been clicked. Look for a confirmation mail or on the site.",
  send_clicked:
    "The assistant says it clicked a send button, but it could not confirm the application. Look for a confirmation mail or on the site.",
  send_unsure:
    "The assistant is not sure whether it clicked a send button, and it could not confirm the application. Look for a confirmation mail or on the site.",
  code_shown:
    "The code you gave showed again, so the assistant stopped, and the site may already have the application. Look for a confirmation mail or on the site.",
};

export const SAID_NOTHING_SENT =
  "The assistant says it sent nothing, but a send button had been clicked, so the app cannot be sure.";

/** The status line of an application the assistant waits on, waited on, or ended with these words; else null. */
export function waitStatusLine(run: Run): string | null {
  if (agentWaits(run))
    return run.state === "submitting"
      ? "A send button was clicked, then the assistant asked for you. It waits for your answer or for you in the browser."
      : "The assistant asked for you. It waits for your answer or for you in the browser.";
  if (agentAsked(run))
    return "The assistant stopped waiting for you. Try again when you are ready, or cancel.";
  if (run.state === "submitted" && run.error_code === "sent_by_person")
    return "Applied: you sent it yourself in the browser, and the site confirmed it.";
  if (run.state === "submit_unknown" && run.error_code)
    return (
      (Object.hasOwn(UNCONFIRMED, run.error_code)
        ? UNCONFIRMED[run.error_code]
        : null) ?? null
    );
  return null;
}
