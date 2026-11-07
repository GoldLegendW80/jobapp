import { describe, expect, it } from "vitest";

import {
  REST,
  SETTLE,
  behindPose,
  cubicBezier,
  leaveDuration,
  settled,
  springStep,
} from "../helpers/deck/motion";

describe("springStep", () => {
  it("brings a released card back to the middle and stops there", () => {
    let moving = { value: 90, velocity: 0 };
    for (let frame = 0; frame < 120; frame += 1)
      moving = springStep(moving, 0, SETTLE, 1 / 60);
    expect(settled(moving, 0)).toBe(true);
  });

  it("carries the speed of a throw past the target before it settles", () => {
    let moving = { value: 0, velocity: 2000 };
    let furthest = 0;
    for (let frame = 0; frame < 60; frame += 1) {
      moving = springStep(moving, 0, SETTLE, 1 / 60);
      furthest = Math.max(furthest, moving.value);
    }
    expect(furthest).toBeGreaterThan(20);
  });
});

describe("cubicBezier", () => {
  it("runs from 0 to 1 and never goes back", () => {
    const ease = cubicBezier(0.23, 1, 0.32, 1);
    expect(ease(0)).toBe(0);
    expect(ease(1)).toBe(1);
    let last = 0;
    for (let t = 0.05; t < 1; t += 0.05) {
      expect(ease(t)).toBeGreaterThanOrEqual(last);
      last = ease(t);
    }
  });
});

describe("leaveDuration", () => {
  it("follows the throw, within bounds", () => {
    expect(leaveDuration(600, 2)).toBe(300);
    expect(leaveDuration(600, 100)).toBe(180);
    expect(leaveDuration(600, 0.1)).toBe(420);
    expect(leaveDuration(600, 0)).toBe(340);
  });
});

describe("behindPose", () => {
  it("rises one place as the card on top leaves", () => {
    expect(behindPose(1, 1)).toEqual(REST);
    expect(behindPose(2, 1)).toEqual(behindPose(1, 0));
    expect(behindPose(1, 0).scale).toBeLessThan(1);
    expect(behindPose(1, 0).y).toBeGreaterThan(0);
  });
});
