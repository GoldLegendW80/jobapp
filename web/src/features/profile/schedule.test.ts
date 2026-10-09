import { describe, expect, it } from "vitest";

import { WEEKDAYS, type SweepSchedule } from "./api";
import { describeSchedule } from "./schedule";

const on = (overrides: Partial<SweepSchedule>): SweepSchedule => ({
  enabled: true,
  days: [...WEEKDAYS],
  times: ["06:17"],
  timezone: "Europe/Paris",
  ...overrides,
});

describe("describeSchedule", () => {
  it("says off whatever the rest", () => {
    expect(describeSchedule({ ...on({}), enabled: false })).toBe("off");
  });

  it.each<[Partial<SweepSchedule>, string]>([
    [{}, "on, every day at 06:17 (Europe/Paris)"],
    [
      { days: ["friday", "monday", "tuesday", "wednesday", "thursday"] },
      "on, weekdays at 06:17 (Europe/Paris)",
    ],
    [{ days: ["sunday"] }, "on, Sunday at 06:17 (Europe/Paris)"],
    [
      {
        days: ["saturday", "monday", "wednesday"],
        times: ["21:00", "07:30", "12:00"],
      },
      "on, Monday, Wednesday and Saturday at 07:30, 12:00 and 21:00 (Europe/Paris)",
    ],
    [{ timezone: "Asia/Tokyo" }, "on, every day at 06:17 (Asia/Tokyo)"],
  ])("%j", (overrides, text) => {
    expect(describeSchedule(on(overrides))).toBe(text);
  });
});
