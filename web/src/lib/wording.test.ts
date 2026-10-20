import { describe, expect, it } from "vitest";

import { CONTRACT_TYPES } from "@/features/jobs/labels";

import { CONTRACT_WORDS, WORDING, contractWord } from "./wording";

const DASHES = /[‒–—―]|\s-\s/;
const JARGON = /\b(ATS|sweep|lease|CDI)\b/;

describe("wording", () => {
  it("uses no dash as a separator in any value", () => {
    for (const value of [
      ...Object.values(WORDING),
      ...Object.values(CONTRACT_WORDS),
    ]) {
      expect(value, value).not.toMatch(DASHES);
    }
  });

  it("has no jargon in a label", () => {
    for (const value of Object.values(WORDING)) {
      expect(value, value).not.toMatch(JARGON);
    }
  });

  it("reads every contract code of the jobs module as a word", () => {
    for (const code of CONTRACT_TYPES) {
      expect(contractWord(code)).toBe(CONTRACT_WORDS[code]);
      expect(CONTRACT_WORDS[code]).toBeTruthy();
    }
    expect(contractWord("CDI")).toBe("Permanent");
    expect(contractWord("Seasonal")).toBe("Seasonal");
  });

  it("names the three thresholds apart", () => {
    const names = [
      WORDING.showPostingsFrom,
      WORDING.writeDocumentsFrom,
      WORDING.applyAutomaticallyFrom,
    ];
    expect(new Set(names).size).toBe(3);
  });
});
