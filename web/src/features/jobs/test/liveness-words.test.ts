import { describe, expect, it } from "vitest";

import type { Liveness } from "../api/liveness-api";
import { checkWords, mayBeClosed } from "../helpers/liveness-words";

const AT = "2026-11-07T09:30:00Z";
const check = (verdict: string, reason: string): Liveness => ({
  verdict,
  reason,
  sentence: null,
  checked_at: AT,
});

describe("mayBeClosed", () => {
  it.each([
    ["found", "may_be_closed", true],
    ["shortlisted", "closed", true],
    ["cv_ready", "may_be_closed", true],
    ["staged", "may_be_closed", true],
    ["closed", "closed", false],
    ["applied", "may_be_closed", false],
    ["interview", "closed", false],
    ["skipped", "may_be_closed", false],
    ["found", "no_closure", false],
    ["found", "unconfirmed", false],
    ["found", "a_verdict_of_a_newer_api", false],
  ])("a %s posting whose check says %s: %s", (status, verdict, shown) => {
    expect(
      mayBeClosed({ status, liveness: check(verdict, "page_hints") }),
    ).toBe(shown);
  });

  it("is false without a check, and true on a card, which has no status", () => {
    expect(mayBeClosed({ status: "found", liveness: null })).toBe(false);
    expect(
      mayBeClosed({ liveness: check("may_be_closed", "page_hints") }),
    ).toBe(true);
  });
});

describe("checkWords", () => {
  it.each([
    [
      "closed",
      "page_says",
      "closed",
      "Checked on 7 Nov 2026: its page says it is closed, so it moved to Closed.",
    ],
    [
      "closed",
      "http_404",
      "closed",
      "Checked on 7 Nov 2026: its page no longer exists, so it moved to Closed.",
    ],
    [
      "closed",
      "page_says",
      "found",
      "Checked on 7 Nov 2026: its page says it is closed.",
    ],
    [
      "may_be_closed",
      "page_hints",
      "found",
      "Checked on 7 Nov 2026: its page may say it is closed.",
    ],
    [
      "may_be_closed",
      "redirected",
      "found",
      "Checked on 7 Nov 2026: its address now leads to another page.",
    ],
    [
      "no_closure",
      "none",
      "found",
      "Checked on 7 Nov 2026: its page shows no sign of closing.",
    ],
    [
      "unconfirmed",
      "sign_in",
      "found",
      "Checked on 7 Nov 2026: the page could not be read, the site asks to sign in first.",
    ],
    [
      "unconfirmed",
      "a_reason_of_a_newer_api",
      "found",
      "Checked on 7 Nov 2026: the page could not be read, for a reason this page does not know.",
    ],
    [
      "unconfirmed",
      "linkedin",
      "found",
      "LinkedIn postings are not checked: LinkedIn asks to sign in first.",
    ],
    [
      "unconfirmed",
      "no_address",
      "found",
      "This posting has no address to check.",
    ],
  ])("%s with %s on a %s posting", (verdict, reason, status, words) => {
    expect(checkWords(check(verdict, reason), status)).toBe(words);
  });

  it("never uses a dash as a separator", () => {
    for (const reason of ["refused", "rate_limited", "bot_check", "busy"])
      expect(checkWords(check("unconfirmed", reason), "found")).not.toMatch(
        /—|–| - /,
      );
  });
});
