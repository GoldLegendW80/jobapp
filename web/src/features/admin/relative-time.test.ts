import { describe, expect, it } from "vitest";

import { exactTime, relativeTime } from "./relative-time";

const NOW = new Date("2026-10-08T12:00:00Z");
const at = (ms: number) => new Date(NOW.getTime() + ms);

describe("relativeTime", () => {
  it.each([
    [7 * 86_400_000, "in 7 days"],
    [7 * 86_400_000 - 1000, "in 7 days"],
    [6.4 * 86_400_000, "in 6 days"],
    [6.6 * 86_400_000, "in 7 days"],
    [86_400_000, "in 1 day"],
    [23 * 3_600_000, "in 23 hours"],
    [3_600_000, "in 1 hour"],
    [3_600_000 - 1000, "in 1 hour"],
    [40 * 60_000, "in 40 minutes"],
    [61_000, "in 1 minute"],
    [58_000, "now"],
    [0, "now"],
    [-58_000, "now"],
    [-60_000, "1 minute ago"],
    [-3 * 3_600_000, "3 hours ago"],
    [-86_400_000, "1 day ago"],
    [-40 * 86_400_000, "40 days ago"],
  ])("%d ms reads %j", (ms, text) => {
    expect(relativeTime(at(ms), NOW)).toBe(text);
  });

  it("reads ISO strings and refuses garbage quietly", () => {
    expect(relativeTime("2026-10-15T12:00:00Z", NOW)).toBe("in 7 days");
    expect(relativeTime("not a date", NOW)).toBe("");
  });

  it("never uses a dash", () => {
    for (const ms of [-1e9, -1e6, 0, 1e6, 1e9])
      expect(relativeTime(at(ms), NOW)).not.toMatch(/[–—]| - /);
  });
});

describe("exactTime", () => {
  it("formats a date and passes garbage through", () => {
    expect(exactTime("2026-10-15T12:00:00Z")).toMatch(/15 Oct 2026/);
    expect(exactTime("nope")).toBe("nope");
  });
});
