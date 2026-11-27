/**
 * The api's routes of the apply agent's wait and messages (A25; api/app/apply/router.py and
 * applier.py: `answer_question`, `answer_code`, `post_message`), in memory, with the api's own
 * statuses and sentences, so the pages are tested against what the api answers. The rules of a
 * code (`code_of`, `looks_like_code`) are ported from applier.py; the page never applies them.
 */
import { http, HttpResponse } from "msw";

import type { Application, ChatLine } from "@/features/apply/api/apply-api";

/** The api's sentences (api/app/apply/applier.py), word for word. */
export const SAID = {
  waitsForNoAnswer: "This application waits for no answer now.",
  codeForm:
    "A code is 6 to 16 letters or digits, given alone (spaces, hyphens, dots or other marks between its groups are fine; in capitals when it has no digit).",
  codeInBrowser:
    "The app types a code of 6 to 16 letters or digits only. Type this one in the browser yourself: open Use the browser myself, then Let the assistant continue.",
  codeAlone:
    "This may hold a code, so it is not sent to the assistant: give a code in the code box, or type it in the browser yourself. Words here go to the assistant as you write them, so write them with no digits and no word in capitals.",
  onItsWay: "A code for this question is on its way already.",
  noCodeAsked: "The assistant asks for no code now.",
  codeNotTaken:
    "The browser could not take the code now. Type it in the browser yourself: open Use the browser myself, then Let the assistant continue.",
  codeSaid:
    "The code you gave, typed by the app on the site and never shown to the assistant.",
  codeShown:
    "The code you gave showed again, on the site or in the assistant's own call, so the assistant stopped: finish in the browser, or apply again.",
  noRun:
    "The assistant is not working on this application now, so it cannot read a message.",
  tooMany:
    "That is a lot of messages at once: wait a minute, then write again.",
  messagesFull:
    "No more messages for this application: it has 100, the most it takes. Use the browser yourself, or wait for the assistant's question.",
  messageLong: "A message is 1,000 characters at most.",
  writeFirst: "Write a message first.",
  plainText: "Use plain text without control characters.",
} as const;

const CODE_SENT_MAX = 64;
const MESSAGE_MAX = 1000;
const MESSAGES_PER_MINUTE = 5;
const MESSAGES_MAX = 100;

const WORD = /[\p{L}\p{N}]+/gu;
const words = (text: string) => text.match(WORD) ?? [];
const tokens = (text: string) => text.split(/\s+/u).filter(Boolean);
const isDigit = (one: string) => /\p{Nd}/u.test(one);
const isUpper = (one: string) => /\p{Lu}/u.test(one);
const allUpper = (group: string) => isUpper(group) && !/\p{Ll}/u.test(group);

/** applier._code_shape: the letters and digits of a code's shape, of any length and script. */
function codeShape(raw: string): string {
  const groupsOf = tokens(raw.normalize("NFKC"))
    .map(words)
    .filter((groups) => groups.length > 0);
  const groups = groupsOf.flat();
  const joined = groups.join("");
  if (groupsOf.length === 1 && Array.from(joined).some(isDigit)) return joined;
  if (
    groups.length > 0 &&
    groups.every((group) => Array.from(group).some(isDigit) || allUpper(group))
  )
    return joined;
  return "";
}

/** applier.code_of: the code as browserd types it, or "" when it is none. */
export const codeOf = (raw: string) => {
  const code = codeShape(raw);
  return /^[0-9A-Za-z]{6,16}$/.test(code) ? code : "";
};

/** applier.looks_like_code: words to a code question that may hold a code. */
export function looksLikeCode(text: string): boolean {
  const folded = text.normalize("NFKC");
  const codeToken = (groups: string[]) => {
    const joined = Array.from(groups.join(""));
    return joined.length >= 4 && joined.some(isDigit);
  };
  if (tokens(folded).some((token) => codeToken(words(token)))) return true;
  let run = 0;
  for (const word of words(folded)) {
    const chars = Array.from(word);
    const codeLike =
      chars.some(isDigit) ||
      chars.slice(1).some(isUpper) ||
      (chars.length === 1 && isUpper(word));
    run = codeLike ? run + chars.length : 0;
    if (run >= 4) return true;
  }
  return false;
}

// bank.saved_text: control and format characters refused, a new line kept.
const plain = (text: string) =>
  !Array.from(text).some(
    (one) => one !== "\n" && /[\p{Cc}\p{Cf}\p{Cs}]/u.test(one),
  );

/** What a route of this fake reads and changes: the application and what its detail adds. */
export type Found = {
  application: Application & { review_agent?: boolean };
  detail: Record<string, unknown>;
};

/** The fake's switches and what the page sent. */
export function agentWaitState() {
  return {
    /** Every body POSTed to the question and messages routes, in order. */
    sent: [] as { route: "question" | "messages"; body: unknown }[],
    /** The browser takes the next code (false: the api's 409 that it could not). */
    browserTakes: true,
    /** The code the person gave waits on the lease to be typed (applier._code_pending). */
    codeWaits: false,
    /** Another tab's code for the open ask is on its way. */
    onItsWay: false,
    /** The time of the next line of the chat, as the api writes it. */
    now: "2026-11-26T10:04:00Z",
  };
}
export type AgentWaitState = ReturnType<typeof agentWaitState>;

const chatOf = (found: Found) => (found.detail.chat ?? []) as ChatLine[];
const say = (found: Found, line: ChatLine) => {
  found.detail.chat = [...chatOf(found), line];
};
const refuse = (status: number, detail: string) =>
  HttpResponse.json({ detail }, { status });

const waits = (application: Application) =>
  application.needs === "agent_waits" &&
  (application.state === "needs_person" || application.state === "submitting");

/** applier.open_ask: the ask the run waits on, with no answer yet. */
function openAsk(found: Found): { kind: string } | null {
  const question = found.detail.question as
    { kind: string; said_by?: string; live?: boolean } | null | undefined;
  if (!waits(found.application) || question?.said_by !== "assistant")
    return null;
  const last = chatOf(found)
    .filter((line) => !(line.who === "person" && line.kind === "message"))
    .at(-1);
  return last?.who === "assistant" && !last.end ? question : null;
}

/** applier.under_way: a run of the apply agent fills, sends or waits. */
function underWay({ application, detail }: Found): boolean {
  const reviewAgent =
    application.review_agent === true || detail.review_agent === true;
  const agent =
    !application.dry_run &&
    application.ats !== "email" &&
    (application.mode === "direct" ||
      (application.mode === "review" && reviewAgent));
  return (
    agent &&
    (application.state === "filling" ||
      application.state === "submitting" ||
      waits(application))
  );
}

/**
 * The handlers of POST question (`{answer}` or `{code}` alone) and POST messages (`{text}`) on
 * the application `find` returns (null: none, or another person's, 404).
 */
export function agentWaitRoutes(
  find: (id: string) => Found | null,
  state: AgentWaitState,
) {
  return [
    http.post(
      "*/api/applications/:id/question",
      async ({ params, request }) => {
        const body = (await request.json()) as {
          answer?: string;
          remember?: boolean;
          code?: string;
        };
        state.sent.push({ route: "question", body });
        const found = find(String(params.id));
        if (typeof body.code === "string") {
          if (body.answer || body.remember || body.code.length > CODE_SENT_MAX)
            return refuse(422, SAID.codeForm);
          if (!found) return refuse(404, "No such application.");
          const ask = openAsk(found);
          if (!ask) return refuse(409, SAID.waitsForNoAnswer);
          if (ask.kind !== "code") return refuse(409, SAID.noCodeAsked);
          if (state.onItsWay) return refuse(409, SAID.onItsWay);
          if (!codeOf(body.code))
            return refuse(
              422,
              codeShape(body.code) ? SAID.codeInBrowser : SAID.codeForm,
            );
          if (!state.browserTakes) return refuse(409, SAID.codeNotTaken);
          state.codeWaits = true;
          say(found, {
            at: state.now,
            who: "person",
            kind: "code",
            text: SAID.codeSaid,
            end: null,
          });
          return HttpResponse.json(found.application);
        }
        if (!found) return refuse(404, "No such application.");
        const ask = openAsk(found);
        if (!ask) return refuse(409, SAID.waitsForNoAnswer);
        const answer = (body.answer ?? "").slice(0, 5000);
        if (!plain(answer)) return refuse(422, SAID.plainText);
        if (!answer.trim()) return refuse(422, "Type an answer.");
        if (ask.kind === "code" && state.onItsWay)
          return refuse(409, SAID.onItsWay);
        if (ask.kind === "code" && looksLikeCode(answer))
          return refuse(422, SAID.codeAlone);
        say(found, {
          at: state.now,
          who: "person",
          kind: "answer",
          text: answer.trim(),
          end: null,
        });
        return HttpResponse.json(found.application);
      },
    ),
    http.post(
      "*/api/applications/:id/messages",
      async ({ params, request }) => {
        const body = (await request.json()) as { text: string };
        state.sent.push({ route: "messages", body });
        const found = find(String(params.id));
        if (!found) return refuse(404, "No such application.");
        if (!underWay(found)) return refuse(409, SAID.noRun);
        const text = body.text;
        if (Array.from(text).length > MESSAGE_MAX)
          return refuse(422, SAID.messageLong);
        if (!text.trim()) return refuse(422, SAID.writeFirst);
        if (!plain(text)) return refuse(422, SAID.plainText);
        const codePending = openAsk(found)?.kind === "code" || state.codeWaits;
        if (codePending && looksLikeCode(text))
          return refuse(422, SAID.codeAlone);
        const said = chatOf(found).filter(
          (line) => line.who === "person" && line.kind === "message",
        );
        // The cap comes before the rate; the rate counts the last minute only.
        if (said.length >= MESSAGES_MAX) return refuse(429, SAID.messagesFull);
        const since = Date.parse(state.now) - 60_000;
        const recent = said.filter((line) => Date.parse(line.at ?? "") > since);
        if (recent.length >= MESSAGES_PER_MINUTE)
          return refuse(429, SAID.tooMany);
        const line: ChatLine = {
          at: state.now,
          who: "person",
          kind: "message",
          text: text.trim(),
          end: null,
          read: false,
        };
        say(found, line);
        return HttpResponse.json(line, { status: 201 });
      },
    ),
  ];
}

/** The run read the person's messages with a tool call's answer (applier.take_messages). */
export function readMessages(detail: Record<string, unknown>) {
  detail.chat = ((detail.chat ?? []) as ChatLine[]).map((line) =>
    line.who === "person" && line.kind === "message"
      ? { ...line, read: true }
      : line,
  );
}
