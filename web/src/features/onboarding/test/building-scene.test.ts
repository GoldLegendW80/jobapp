import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const web = path.resolve(__dirname, "../../../..");

describe("the scene of Building your profile", () => {
  it("ships built, with the artboard, its state machine and the two inputs the page drives", () => {
    const riv = readFileSync(
      path.join(web, "src/features/onboarding/assets/building.riv"),
    ).toString("latin1");
    expect(riv.startsWith("RIVE")).toBe(true);
    for (const name of ["Building", "progress", "finish"])
      expect(riv).toContain(name);
  });
});
