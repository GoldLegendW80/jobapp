import { describe, expect, it } from "vitest";

import {
  DEFAULT_SCHEDULE,
  formatInZone,
  nextRun,
  zonedInstant,
  type SweepSchedule,
} from "./schedule";

const on = (fields: Partial<SweepSchedule> = {}): SweepSchedule => ({
  ...DEFAULT_SCHEDULE,
  enabled: true,
  ...fields,
});
const at = (iso: string) => new Date(iso);
const wall = (y: number, m: number, d: number, h: number, mi: number) => ({
  year: y,
  month: m,
  day: d,
  hour: h,
  minute: mi,
});

describe("zonedInstant, the same instants as api/app/jobs/schedule.py", () => {
  it.each([
    [wall(2026, 10, 10, 6, 17), "2026-10-10T04:17:00.000Z"],
    [wall(2026, 12, 1, 6, 17), "2026-12-01T05:17:00.000Z"],
    // The skipped spring hour fires as the clock jumps (03:00 local).
    [wall(2026, 3, 29, 2, 30), "2026-03-29T01:00:00.000Z"],
    [wall(2026, 3, 29, 2, 0), "2026-03-29T01:00:00.000Z"],
    [wall(2026, 3, 29, 1, 59), "2026-03-29T00:59:00.000Z"],
    // The repeated autumn hour: its first occurrence.
    [wall(2026, 10, 25, 2, 30), "2026-10-25T00:30:00.000Z"],
    [wall(2026, 10, 25, 3, 0), "2026-10-25T02:00:00.000Z"],
  ])("Paris %o is %s", (local, expected) => {
    expect(new Date(zonedInstant(local, "Europe/Paris")).toISOString()).toBe(
      expected,
    );
  });

  it("New York's transitions", () => {
    expect(
      new Date(
        zonedInstant(wall(2026, 3, 8, 2, 30), "America/New_York"),
      ).toISOString(),
    ).toBe("2026-03-08T07:00:00.000Z");
    expect(
      new Date(
        zonedInstant(wall(2026, 11, 1, 1, 30), "America/New_York"),
      ).toISOString(),
    ).toBe("2026-11-01T05:30:00.000Z");
  });
});

describe("nextRun", () => {
  it("is null when off", () => {
    expect(nextRun(DEFAULT_SCHEDULE, at("2026-10-09T12:00:00Z"))).toBeNull();
  });

  it("finds the next weekday and time in the schedule's zone", () => {
    const weekly = on({
      days: ["sunday"],
      times: ["09:00"],
      timezone: "America/New_York",
    });
    expect(nextRun(weekly, at("2026-10-09T12:00:00Z"))?.toISOString()).toBe(
      "2026-10-11T13:00:00.000Z",
    );
    // Exactly at a slot: the next one.
    expect(nextRun(weekly, at("2026-10-11T13:00:00Z"))?.toISOString()).toBe(
      "2026-10-18T13:00:00.000Z",
    );
  });

  it("takes the earliest of several times, later today first", () => {
    const twice = on({ times: ["06:17", "18:30"] });
    expect(nextRun(twice, at("2026-10-10T05:00:00Z"))?.toISOString()).toBe(
      "2026-10-10T16:30:00.000Z",
    );
    expect(nextRun(twice, at("2026-10-10T17:00:00Z"))?.toISOString()).toBe(
      "2026-10-11T04:17:00.000Z",
    );
  });

  it("crosses daylight saving time", () => {
    const night = on({ times: ["02:30"] });
    expect(nextRun(night, at("2026-03-28T23:00:00Z"))?.toISOString()).toBe(
      "2026-03-29T01:00:00.000Z",
    );
    expect(nextRun(night, at("2026-10-24T23:00:00Z"))?.toISOString()).toBe(
      "2026-10-25T00:30:00.000Z",
    );
  });
});

describe("words", () => {
  it("formats an instant in the schedule's zone", () => {
    expect(formatInZone(at("2026-10-10T04:17:00Z"), "Europe/Paris")).toBe(
      "Sat 10 Oct, 06:17 (Europe/Paris)",
    );
  });

  it("takes the empty profile's schedule as the default", () => {
    expect(DEFAULT_SCHEDULE).toEqual({
      enabled: false,
      days: [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
      ],
      times: ["06:17"],
      timezone: "Europe/Paris",
    });
  });
});
