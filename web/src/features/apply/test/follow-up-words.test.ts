import { describe, expect, it } from "vitest";

import { cold, followUp, waiting } from "@/design-system/fixtures/follow-ups";

import {
  MAILTO_MAX,
  cadenceLine,
  datesLine,
  mailtoHref,
} from "../helpers/follow-up-words";

const TODAY = "2026-11-07";
const DASHES = /[–—]| - /;

describe("the words of a follow up", () => {
  it("says where each cadence stands, as facts", () => {
    const lines = [
      cadenceLine(followUp(), TODAY),
      cadenceLine(followUp({ next_on: TODAY }), TODAY),
      cadenceLine(followUp({ count: 1 }), TODAY),
      cadenceLine(waiting(), TODAY),
      cadenceLine(cold(), TODAY),
      cadenceLine(followUp({ urgency: "answered", next_on: null }), TODAY),
      cadenceLine(followUp({ kind: "thank_you" }), TODAY),
      cadenceLine(followUp({ kind: "thank_you", urgency: "done" }), TODAY),
    ];
    expect(lines).toEqual([
      "First follow up due since 4 Nov 2026.",
      "First follow up due today.",
      "Second follow up due since 4 Nov 2026.",
      "First follow up due on 10 Nov 2026.",
      "No answer after two follow ups. The app suggests no third one.",
      "The company wrote back. Their mail waits for your reply.",
      "A thank you note for the interview is due since 4 Nov 2026.",
      "Thank you note sent.",
    ]);
    expect(lines.filter((line) => DASHES.test(line))).toEqual([]);
  });

  it("counts the follow ups sent of the two at most", () => {
    expect(datesLine(cold())).toBe(
      "Applied on 20 Sept 2026. Follow ups sent: 2 of 2.",
    );
    expect(datesLine(followUp({ kind: "thank_you" }))).toBe(
      "Applied on 28 Oct 2026.",
    );
  });
});

describe("the address that opens the draft in the person's mail", () => {
  it("holds the subject and the whole text when it is short", () => {
    const { href, cut } = mailtoHref("Data Engineer", "Hello,\n\nThanks.");
    expect(cut).toBe(false);
    expect(href).toBe(
      "mailto:?subject=Data%20Engineer&body=Hello%2C%0A%0AThanks.",
    );
  });

  it("is cut under the limit, never inside a character", () => {
    const body = "\u{1F600}".repeat(400) + "é".repeat(400);
    const { href, cut } = mailtoHref("Subject", body);
    expect(cut).toBe(true);
    expect(href.length).toBeLessThanOrEqual(MAILTO_MAX);
    expect(() =>
      decodeURIComponent(href.split("body=")[1] ?? ""),
    ).not.toThrow();
  });
});
