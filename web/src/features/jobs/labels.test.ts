import { describe, expect, it } from "vitest";

import { SOURCE_PROBLEMS, sourceLabel } from "./labels";

describe("sourceLabel", () => {
  it("names one browser source in words", () => {
    expect(sourceLabel("linkedin:AI Engineer/Paris")).toBe(
      "LinkedIn, AI Engineer, Paris",
    );
    expect(sourceLabel("greenhouse:example")).toBe("greenhouse:example");
  });

  it("names every source of a posting found twice (browser B10)", () => {
    expect(sourceLabel("greenhouse:example, linkedin:AI Engineer")).toBe(
      "greenhouse:example; LinkedIn, AI Engineer",
    );
    expect(sourceLabel("linkedin:AI Engineer, greenhouse:example")).toBe(
      "LinkedIn, AI Engineer; greenhouse:example",
    );
  });

  it("keeps a comma inside search words", () => {
    expect(sourceLabel("linkedin:AI Engineer, Agents")).toBe(
      "LinkedIn, AI Engineer, Agents",
    );
  });
});

describe("SOURCE_PROBLEMS", () => {
  it("says each LinkedIn refusal in words, without dashes", () => {
    for (const code of [
      "linkedin_off",
      "linkedin_paused",
      "linkedin_daily",
      "linkedin_signin",
      "linkedin_checkpoint",
    ]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/[–—]| - /);
    }
  });
});
