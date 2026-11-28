import { describe, expect, it } from "vitest";

import { matchBand } from "../helpers/match-bands";

describe("matchBand", () => {
  it.each([
    [100, "Strong"],
    [80, "Strong"],
    [79, "Good"],
    [60, "Good"],
    [59, "Partial"],
    [40, "Partial"],
    [39, "Weak"],
    [0, "Weak"],
  ])("reads %i as %s", (score, word) => {
    expect(matchBand(score)).toBe(word);
  });
});
