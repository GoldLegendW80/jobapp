import { describe, expect, it } from "vitest";

import {
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
} from "@/design-system/fixtures/agent-wait";
import { fakeApplication } from "@/test/apply";

import {
  askEndLine,
  boxOf,
  CODE_IN_BROWSER,
  kindWords,
  LATE_ANSWER,
  NO_PASSWORD,
  saidNothingSent,
  SHOWN_MAX,
  shownText,
  showsAsk,
  stoppedLine,
  waitStatusLine,
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

  it("offers the box while the run waits, the browser for a code, and nothing twice", () => {
    expect(boxOf(SIGNIN_ASK, chatWith(SIGNIN_ASK))).toBe("answer");
    expect(boxOf(CODE_ASK, chatWith(CODE_ASK))).toBe("browser");
    const answered = [
      ...chatWith(SIGNIN_ASK),
      { at: null, who: "person", kind: "answer", text: "Yes", end: null },
    ];
    expect(boxOf(SIGNIN_ASK, answered)).toBe("sent");
    expect(boxOf({ ...SIGNIN_ASK, live: false }, chatWith(SIGNIN_ASK))).toBe(
      null,
    );
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
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 12 }),
    ).toBe(
      "The browser is yours until you choose Let the assistant continue, for 12 more minutes at most: then the assistant has it back.",
    );
    expect(
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 1 }),
    ).toMatch(/for 1 more minute at most/);
    expect(
      takeoverLine({ wait_reason: "ask_person", wait_minutes_left: 12 }),
    ).toBeNull();
  });

  it("writes no dash as a separator", () => {
    for (const line of [
      CODE_IN_BROWSER,
      LATE_ANSWER,
      NO_PASSWORD,
      waitStatusLine(waits()),
      waitStatusLine(waits({ needs: "agent_asked" })),
      stoppedLine(chatWith(SIGNIN_ASK, "interrupted")),
      takeoverLine({ wait_reason: "takeover", wait_minutes_left: 3 }),
    ])
      expect(line).not.toMatch(SEPARATOR);
  });
});
