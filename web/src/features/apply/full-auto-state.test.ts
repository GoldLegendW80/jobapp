import { describe, expect, it } from "vitest";

import {
  clockText,
  holdLeft,
  readMinutes,
  serverOffset,
  spokenLeft,
  summaryLine,
} from "./full-auto-state";

const SEPARATOR = /[–—]| - /;

describe("the hold's countdown", () => {
  it("reads the server's clock, not the page's", () => {
    const received = Date.parse("2030-01-01T00:00:00Z");
    const offset = serverOffset("2026-10-14T09:00:00Z", received);
    expect(offset).toBe(
      Date.parse("2026-10-14T09:00:00Z") - Date.parse("2030-01-01T00:00:00Z"),
    );
    // 90 s after the answer arrived, on the page's clock: 90 s of the hold have gone.
    expect(holdLeft("2026-10-14T09:15:00Z", offset, received + 90_000)).toBe(
      810_000,
    );
  });

  it("reads no offset when the server sent no time", () => {
    expect(serverOffset(undefined, 1_000)).toBe(0);
    expect(serverOffset("not a date", 1_000)).toBe(0);
    expect(serverOffset("2026-10-14T09:00:00Z", 0)).toBe(0);
  });

  it("never goes below zero, and an unreadable end is over", () => {
    expect(holdLeft("2026-10-14T09:00:00Z", 0, Date.parse("2026-10-15"))).toBe(
      0,
    );
    expect(holdLeft("soon", 0, 0)).toBe(0);
  });

  it.each([
    [0, "0:00"],
    [1, "0:01"],
    [999, "0:01"],
    [1000, "0:01"],
    [59_001, "1:00"],
    [600_000, "10:00"],
    [899_500, "15:00"],
    [3_600_000, "1:00:00"],
    [86_400_000, "24:00:00"],
    [-5, "0:00"],
  ])("shows %i ms as %s", (ms, text) => {
    expect(clockText(ms)).toBe(text);
  });

  it.each([
    [0, "less than a minute"],
    [59_000, "less than a minute"],
    [61_000, "about 2 minutes"],
    [600_000, "about 10 minutes"],
    [3_600_000, "about 1 hour"],
    [5_400_000, "about 2 hours"],
  ])("says %i ms as %s", (ms, text) => {
    expect(spokenLeft(ms)).toBe(text);
  });
});

describe("the hold length typed", () => {
  it.each([
    ["15", 15, 15],
    [" 20 ", 15, 20],
    ["1440", 1, 1440],
    ["1", 1, 1],
  ])("takes %j from %i", (text, min, value) => {
    expect(readMinutes(text, min)).toBe(value);
  });

  it.each([
    ["14", 15],
    ["1441", 1],
    ["", 1],
    ["ten", 1],
    ["12.5", 1],
    ["-15", 1],
    ["1e2", 1],
    ["００15", 1],
  ])("refuses %j from %i", (text, min) => {
    expect(readMinutes(text, min)).toBeNull();
  });
});

describe("the summary line", () => {
  const empty = {
    since: "2026-10-14T00:00:00Z",
    submitted: 0,
    held: 0,
    sent_to_review: [],
  };

  it("is nothing when nothing happened", () => {
    expect(summaryLine(empty)).toBeNull();
  });

  it("counts every reason sent to review", () => {
    const line = summaryLine({
      ...empty,
      submitted: 1,
      sent_to_review: [
        { code: "fields_left", sentence: null, count: 2 },
        { code: "captcha_seen", sentence: null, count: 1 },
      ],
    });
    expect(line).toBe("1 submitted, 0 held, 3 sent to you for review");
    expect(line).not.toMatch(SEPARATOR);
  });
});
