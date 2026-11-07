import { describe, expect, it } from "vitest";

import {
  FLICK_MIN_DISTANCE,
  FLICK_VELOCITY,
  MAX_TILT,
  SWIPE_THRESHOLD,
  initials,
  lean,
  releaseOutcome,
  releaseVelocity,
  stampOpacity,
  tilt,
} from "../helpers/deck/gesture";

describe("releaseOutcome", () => {
  it.each([
    [SWIPE_THRESHOLD, 0, "like"],
    [-SWIPE_THRESHOLD, 0, "pass"],
    [SWIPE_THRESHOLD - 1, 0, null],
    [-(SWIPE_THRESHOLD - 1), 0, null],
    [0, 0, null],
  ] as const)("a release at %d px, still, is %s", (dx, vx, outcome) => {
    expect(releaseOutcome(dx, vx)).toBe(outcome);
  });

  it("takes a flick in the direction of the drag before the threshold", () => {
    expect(releaseOutcome(FLICK_MIN_DISTANCE, FLICK_VELOCITY)).toBe("like");
    expect(releaseOutcome(-FLICK_MIN_DISTANCE, -FLICK_VELOCITY)).toBe("pass");
  });

  it("ignores a flick too slow, too short or against the drag", () => {
    expect(
      releaseOutcome(FLICK_MIN_DISTANCE, FLICK_VELOCITY - 0.01),
    ).toBeNull();
    expect(
      releaseOutcome(FLICK_MIN_DISTANCE - 1, FLICK_VELOCITY * 3),
    ).toBeNull();
    expect(releaseOutcome(FLICK_MIN_DISTANCE + 20, -FLICK_VELOCITY)).toBeNull();
  });

  it("springs back a card past the threshold thrown back toward the middle", () => {
    expect(releaseOutcome(SWIPE_THRESHOLD + 40, -FLICK_VELOCITY)).toBeNull();
    expect(releaseOutcome(-(SWIPE_THRESHOLD + 40), FLICK_VELOCITY)).toBeNull();
    // A slower move back is only a hesitation: the card still goes.
    expect(releaseOutcome(SWIPE_THRESHOLD + 40, -FLICK_VELOCITY / 2)).toBe(
      "like",
    );
  });
});

describe("releaseVelocity", () => {
  it("reads the last tenth of a second of the drag", () => {
    const samples = [
      { x: 0, y: 0, t: 0 },
      { x: 100, y: 0, t: 400 },
      { x: 160, y: 20, t: 450 },
      { x: 200, y: 40, t: 500 },
    ];
    expect(releaseVelocity(samples)).toEqual({ vx: 1, vy: 0.4 });
  });

  it("says nothing when the samples span less than a frame", () => {
    expect(
      releaseVelocity([
        { x: 0, y: 0, t: 100 },
        { x: 80, y: 0, t: 105 },
      ]),
    ).toEqual({ vx: 0, vy: 0 });
    expect(releaseVelocity([])).toEqual({ vx: 0, vy: 0 });
  });
});

describe("the look of a drag", () => {
  it("tilts the way it goes, the other way when held low, never past the limit", () => {
    expect(tilt(60)).toBeGreaterThan(0);
    expect(tilt(-60)).toBeLessThan(0);
    expect(tilt(60, true)).toBe(-tilt(60));
    expect(tilt(10_000)).toBe(MAX_TILT);
    expect(tilt(-10_000)).toBe(-MAX_TILT);
  });

  it("leans from -1 to 1, full at the threshold", () => {
    expect(lean(0)).toBe(0);
    expect(lean(SWIPE_THRESHOLD / 2)).toBe(0.5);
    expect(lean(SWIPE_THRESHOLD * 3)).toBe(1);
    expect(lean(-SWIPE_THRESHOLD * 3)).toBe(-1);
  });

  it("shows a stamp from a slight move, fully at the threshold", () => {
    expect(stampOpacity(8)).toBe(0);
    expect(stampOpacity(SWIPE_THRESHOLD / 2)).toBeGreaterThan(0);
    expect(stampOpacity(SWIPE_THRESHOLD / 2)).toBeLessThan(1);
    expect(stampOpacity(-SWIPE_THRESHOLD)).toBe(1);
  });
});

describe("initials", () => {
  it.each([
    ["Lumen Freight", "LF"],
    ["Kestrel Health", "KH"],
    ["Acme", "AC"],
    ["Example Robotics & Co", "ER"],
    ["  ", "?"],
  ])("%s is %s", (company, letters) => {
    expect(initials(company)).toBe(letters);
  });
});
