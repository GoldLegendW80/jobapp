import { describe, expect, it } from "vitest";

import { absoluteTime, relativeTime } from "./when";

const NOW = Date.parse("2026-10-09T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it.each([
    [0, "just now"],
    [59, "just now"],
    [-3600, "just now"], // a clock running ahead of the server
    [60, "1 minute ago"],
    [125, "2 minutes ago"],
    [3600, "1 hour ago"],
    [5 * 3600, "5 hours ago"],
    [24 * 3600, "yesterday"],
    [3 * 24 * 3600, "3 days ago"],
    [8 * 24 * 3600, "last week"],
    [40 * 24 * 3600, "last month"],
    [400 * 24 * 3600, "last year"],
    [800 * 24 * 3600, "2 years ago"],
  ])("%i seconds ago is %s", (seconds, text) => {
    expect(relativeTime(ago(seconds), NOW)).toBe(text);
  });

  it("is empty for an unreadable date", () => {
    expect(relativeTime("not a date", NOW)).toBe("");
  });
});

describe("absoluteTime", () => {
  it("shows the day first, in the time zone asked", () => {
    expect(absoluteTime("2026-10-09T08:05:00Z", "UTC")).toBe(
      "9 Oct 2026, 08:05",
    );
    expect(absoluteTime("2026-10-09T23:30:00Z", "Europe/Paris")).toBe(
      "10 Oct 2026, 01:30",
    );
  });

  it("gives back an unreadable value as it is", () => {
    expect(absoluteTime("soon")).toBe("soon");
  });
});
