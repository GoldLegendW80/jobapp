import { describe, expect, it } from "vitest";

import { CONTRACT_TYPES } from "@/features/jobs/labels";

import {
  CONTRACT_WORDS,
  WORDING,
  SENIORITY_WORDS,
  COMPANY_WORDS,
  REMOTE_WORDS,
  LANGUAGE_WORDS,
  TONE_WORDS,
  WIZARD_STEP_WORDS,
  FEED_NAMES,
  FEED_SHORT_NAMES,
  contractWord,
  deckEmpty,
} from "./wording";

const DASHES = /[‒–—―]|\s-\s/;
const JARGON = /\b(ATS|sweep|lease|CDI)\b/;

describe("wording", () => {
  it("uses no dash as a separator in any value", () => {
    for (const value of [
      ...Object.values(WORDING),
      ...Object.values(CONTRACT_WORDS),
      ...[
        SENIORITY_WORDS,
        COMPANY_WORDS,
        REMOTE_WORDS,
        LANGUAGE_WORDS,
        TONE_WORDS,
        WIZARD_STEP_WORDS,
        FEED_NAMES,
        FEED_SHORT_NAMES,
      ].flatMap((table) => Object.values(table)),
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

  it("pins the whole contract table to the spec words", () => {
    expect(CONTRACT_WORDS).toEqual({
      CDI: "Permanent",
      CDD: "Fixed term",
      VIE: "VIE",
      Stage: "Internship",
      Alternance: "Apprenticeship",
      Freelance: "Freelance mission",
    });
  });

  it("names the threshold in the empty deck line", () => {
    expect(deckEmpty(70)).toBe("Nothing new above your threshold of 70.");
    expect(deckEmpty(70)).not.toMatch(DASHES);
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
