import { describe, expect, it } from "vitest";

import {
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
} from "@/design-system/fixtures/agent-wait";
import { fakeApplication } from "@/test/apply";

import type { ChatLine } from "../api/apply-api";
import {
  askEndLine,
  assistantWorks,
  boxOf,
  CODE_ASKED_AGAIN,
  CODE_BY_HAND,
  CODE_TYPED_BY_APP,
  codeAskedAgain,
  kindWords,
  NO_CODE_HERE,
  NO_PASSWORD,
  readLine,
  saidNothingSent,
  SHOWN_MAX,
  shownText,
  showsAsk,
  stoppedLine,
  unread,
  waitStatusLine,
  WORDS_NOT_CODE,
} from "../helpers/agent-wait";
import { askRetryOf, cancelKindOf } from "../helpers/apply-section-rules";
import { liveStatus, takeoverLine } from "../helpers/live-view";

const SEPARATOR = /[–—]| - /;
const waits = (overrides = {}) =>
  fakeApplication("needs_person", {
    mode: "direct",
    needs: "agent_waits",
    ...overrides,
  });

/** The api's line of a code the person gave (applier.CODE_SAID), never the code. */
const CODE_LINE: ChatLine = {
  at: "2026-11-26T10:04:00Z",
  who: "person",
  kind: "code",
  text: "The code you gave, typed by the app on the site and never shown to the assistant.",
  end: null,
};
const message = (read: boolean): ChatLine => ({
  at: "2026-11-26T10:05:00Z",
  who: "person",
  kind: "message",
  text: "The site is slow today, take your time.",
  end: null,
  read,
});

describe("the apply agent's wait, in words (A25)", () => {
  it("keeps the assistant's words plain, on one line and bounded", () => {
    expect(shownText("Sign in\n\nhere,‮please\u0007")).toBe(
      "Sign in here, please",
    );
    expect(shownText("a؜b⁠c⁤d")).toBe("a b c d");
    const long = "x".repeat(SHOWN_MAX * 4);
    const shown = shownText(long);
    expect(Array.from(shown)).toHaveLength(SHOWN_MAX + 1);
    expect(shown.endsWith("…")).toBe(true);
    // An emoji keeps its variation selector, and a code point is never cut in two.
    expect(shownText("☎️".repeat(SHOWN_MAX / 2))).toBe(
      "☎️".repeat(SHOWN_MAX / 2),
    );
    expect(shownText("😀".repeat(SHOWN_MAX + 5))).toBe(
      `${"😀".repeat(SHOWN_MAX)}…`,
    );
  });

  it("offers the box while the run waits, the code box for a code, and nothing twice", () => {
    expect(boxOf(SIGNIN_ASK, chatWith(SIGNIN_ASK))).toBe("answer");
    expect(boxOf(CODE_ASK, chatWith(CODE_ASK))).toBe("code");
    const answered = [
      ...chatWith(SIGNIN_ASK),
      { at: null, who: "person", kind: "answer", text: "Yes", end: null },
    ];
    expect(boxOf(SIGNIN_ASK, answered)).toBe("sent");
    expect(boxOf(CODE_ASK, [...chatWith(CODE_ASK), CODE_LINE])).toBe(
      "code_sent",
    );
    expect(boxOf({ ...SIGNIN_ASK, live: false }, chatWith(SIGNIN_ASK))).toBe(
      null,
    );
    // A message of the person is no answer to the ask: the boxes stay.
    expect(boxOf(CODE_ASK, [...chatWith(CODE_ASK), message(false)])).toBe(
      "code",
    );
  });

  it("says a code is asked again only after a code the person gave to an earlier ask", () => {
    const earlier = [...chatWith(CODE_ASK, "answer"), CODE_LINE];
    expect(codeAskedAgain(chatWith(CODE_ASK))).toBe(false);
    expect(codeAskedAgain(earlier)).toBe(false);
    expect(
      codeAskedAgain([
        ...earlier,
        message(true),
        ...chatWith(CODE_ASK).slice(-1),
      ]),
    ).toBe(true);
    expect(
      codeAskedAgain([...earlier, ...chatWith(SIGNIN_ASK).slice(-1)]),
    ).toBe(false);
  });

  it("knows when the assistant works on the application, as the api's messages route does", () => {
    const direct = (state: string, overrides = {}) =>
      fakeApplication(state, { mode: "direct", ...overrides });
    expect(assistantWorks(direct("filling"), false)).toBe(true);
    expect(assistantWorks(direct("submitting"), false)).toBe(true);
    expect(assistantWorks(waits(), false)).toBe(true);
    expect(assistantWorks(waits({ state: "submitting" }), false)).toBe(true);
    expect(assistantWorks(fakeApplication("filling"), true)).toBe(true);
    // Review first by the adapters, Apply myself, a rehearsal, an e-mail, a run not started or over.
    expect(assistantWorks(fakeApplication("filling"), false)).toBe(false);
    expect(assistantWorks(direct("filling", { mode: "manual" }), false)).toBe(
      false,
    );
    expect(assistantWorks(direct("filling", { dry_run: true }), false)).toBe(
      false,
    );
    expect(assistantWorks(direct("filling", { ats: "email" }), false)).toBe(
      false,
    );
    expect(
      assistantWorks(direct("filling", { mode: "full_auto" }), false),
    ).toBe(false);
    for (const state of ["queued", "ready", "failed", "submitted"])
      expect(assistantWorks(direct(state), false)).toBe(false);
    expect(assistantWorks(waits({ needs: "agent_asked" }), false)).toBe(false);
  });

  it("says whether the assistant read a message only as the api says it", () => {
    expect(readLine(message(true))).toBe("The assistant has read it.");
    expect(readLine(message(false))).toBe("The assistant has not read it yet.");
    expect(readLine({ ...message(false), read: null })).toBeNull();
    expect(readLine(CODE_LINE)).toBeNull();
    expect(unread([message(true)])).toBe(false);
    expect(unread([message(true), message(false)])).toBe(true);
    expect(unread([{ ...message(false), read: undefined }])).toBe(false);
  });

  it("names each kind in plain words, and a kind it does not know as other", () => {
    expect(kindWords("confirmation_link")).toBe("a confirmation link");
    expect(kindWords("payment")).toBe(kindWords("other"));
    expect(kindWords("toString")).toBe(kindWords("other"));
  });

  it("says each end of an ask and of the wait", () => {
    expect(askEndLine("browser")).toBe(
      "You used the browser, then let the assistant continue.",
    );
    expect(askEndLine("something_new")).toBeNull();
    expect(stoppedLine(chatWith(SIGNIN_ASK, "run_ended"))).toMatch(
      /run ended while it waited/,
    );
    expect(stoppedLine(chatWith(SIGNIN_ASK, "person_late"))).toMatch(
      /^Nobody answered the assistant in time/,
    );
  });

  it("gives the status line of each wait and end, and none to a code it does not know", () => {
    expect(waitStatusLine(waits())).toMatch(/^The assistant asked for you/);
    expect(waitStatusLine(waits({ state: "submitting" }))).toMatch(
      /^A send button was clicked, then the assistant asked/,
    );
    expect(waitStatusLine(waits({ needs: "agent_asked" }))).toMatch(
      /^The assistant stopped waiting/,
    );
    expect(
      waitStatusLine(
        fakeApplication("submit_unknown", {
          error_code: "cancelled_by_person",
        }),
      ),
    ).toBeNull();
    expect(
      waitStatusLine(
        fakeApplication("submit_unknown", { error_code: "toString" }),
      ),
    ).toBeNull();
    expect(
      waitStatusLine(
        fakeApplication("submit_unknown", { error_code: "code_shown" }),
      ),
    ).toMatch(/^The code you gave showed again, so the assistant stopped/);
    expect(
      showsAsk(
        fakeApplication("submit_unknown", { error_code: "person_late" }),
      ),
    ).toBe(true);
    expect(showsAsk(waits({ mode: "direct", needs: "agent_question" }))).toBe(
      false,
    );
  });

  it("reads whether the assistant said nothing was sent from its last end", () => {
    const unknown = fakeApplication("submit_unknown");
    const done = (sent: string) => ({
      at: "2026-11-26T10:00:00Z",
      kind: "agent_done",
      data: { sent },
    });
    expect(saidNothingSent(unknown, [done("no")])).toBe(true);
    expect(saidNothingSent(unknown, [done("no"), done("yes")])).toBe(false);
    expect(saidNothingSent(fakeApplication("failed"), [done("no")])).toBe(
      false,
    );
  });

  it("cancels and tries again in words that fit what may have been sent", () => {
    expect(cancelKindOf(waits())).toBe("cancel");
    expect(cancelKindOf(waits(), true)).toBe("cancelAfterSend");
    expect(cancelKindOf(fakeApplication("cancelled"), true)).toBeNull();
    const asked = waits({ needs: "agent_asked" });
    expect(askRetryOf(asked, SIGNIN_ASK)).toBe("retryAsked");
    expect(askRetryOf(asked, { ...SIGNIN_ASK, after_send: true })).toBe(
      "retryAskedAfterSend",
    );
    expect(askRetryOf(waits(), SIGNIN_ASK)).toBeNull();
  });

  it("says over the live view that the assistant waits, and how long a take over lasts", () => {
    expect(liveStatus("apply_agent", waits())).toMatch(
      /^The assistant waits for you/,
    );
    expect(
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 12 }, true),
    ).toBe(
      "The browser is yours until you choose Let the assistant continue, for 12 more minutes at most: then the assistant has it back.",
    );
    expect(
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 1 }, true),
    ).toMatch(/for 1 more minute at most/);
    // An adapter's fill taken over: no assistant waits to have it back.
    expect(
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 12 }),
    ).toBe(
      "The browser is yours until you choose Let the assistant continue, for 12 more minutes at most.",
    );
    expect(
      takeoverLine({ wait_reason: "ask_person", wait_minutes_left: 12 }),
    ).toBeNull();
  });

  it("writes no dash as a separator", () => {
    for (const line of [
      CODE_ASKED_AGAIN,
      CODE_BY_HAND,
      CODE_TYPED_BY_APP,
      WORDS_NOT_CODE,
      readLine(message(true)),
      readLine(message(false)),
      waitStatusLine(
        fakeApplication("submit_unknown", { error_code: "code_shown" }),
      ),
      NO_CODE_HERE,
      NO_PASSWORD,
      waitStatusLine(waits()),
      waitStatusLine(waits({ needs: "agent_asked" })),
      stoppedLine(chatWith(SIGNIN_ASK, "interrupted")),
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 3 }, true),
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 3 }),
    ])
      expect(line).not.toMatch(SEPARATOR);
  });
});
