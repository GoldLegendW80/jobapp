import { describe, expect, it } from "vitest";

import { BASE_SOURCES, ORIGINS, WAIT_REASONS } from "./documents-api";
import {
  failedSentence,
  fieldLabel,
  fitLabel,
  isOpen,
  money,
  ORIGIN_LABELS,
  SOURCE_LABELS,
  templateLabel,
  tokenRange,
  WAIT_SENTENCES,
} from "./labels";
import { fakeVersion } from "@/test/documents";

const SEPARATOR = /[–—]| - /;

/** `generate.ERRORS` of the api. */
const FAILURE_CODES = [
  "invalid_output",
  "no_result",
  "token_limit",
  "binding_gone",
  "run_failed",
];

describe("labels", () => {
  it("names every failure code of the api, and the letter as a letter", () => {
    const generic = failedSentence(null);
    for (const code of FAILURE_CODES) {
      expect(failedSentence(code)).not.toBe(generic);
    }
    expect(failedSentence("no_result", "cv")).toContain("usable CV");
    expect(failedSentence("no_result", "letter")).toContain("usable letter");
    expect(failedSentence("invalid_output", "letter")).not.toContain("CV");
  });

  it("has a sentence for every code of the api, none with a dash as separator", () => {
    expect(Object.keys(WAIT_SENTENCES).sort()).toEqual(
      [...WAIT_REASONS].sort(),
    );
    expect(Object.keys(SOURCE_LABELS).sort()).toEqual([...BASE_SOURCES].sort());
    expect(Object.keys(ORIGIN_LABELS).sort()).toEqual([...ORIGINS].sort());
    const all = [
      ...Object.values(WAIT_SENTENCES),
      ...Object.values(SOURCE_LABELS),
      ...Object.values(ORIGIN_LABELS),
      ...[...FAILURE_CODES, null].flatMap((code) => [
        failedSentence(code),
        failedSentence(code, "letter"),
      ]),
      fitLabel(fakeVersion()),
      fitLabel(fakeVersion({ fit: "overflow" })),
      fitLabel(fakeVersion({ fit: null })),
    ];
    for (const text of all) expect(text).not.toMatch(SEPARATOR);
  });

  it.each([
    ["classic", "Classic"],
    ["two-column", "Two column"],
    ["upload-ab12cd34", "From your CV"],
    ["", ""],
    ["-", "-"],
  ])("names the template %j as %j", (slug, label) => {
    expect(templateLabel(slug)).toBe(label);
  });

  it.each([
    ["highlights.1", "Highlights, item 2"],
    ["summary", "Summary"],
    ["experience.0.bullets.2", "Experience, item 1, bullets, item 3"],
    ["", "Document"],
    ["skills.0.items.0", "Skills, item 1, items, item 1"],
  ])("reads the path %j as %j", (path, label) => {
    expect(fieldLabel(path)).toBe(label);
  });

  it("tells an open generation from a closed one", () => {
    for (const status of ["waiting", "queued", "running"] as const)
      expect(isOpen({ status, wait_reason: null })).toBe(true);
    for (const status of ["done", "failed", "cancelled"] as const)
      expect(isOpen({ status, wait_reason: null })).toBe(false);
    expect(isOpen(null)).toBe(false);
    expect(isOpen(undefined)).toBe(false);
  });

  it("formats tokens and money", () => {
    expect(tokenRange({ low: 1200, high: 20000 })).toBe(
      "about 1,200 to 20,000 tokens",
    );
    expect(money("USD", "0.1")).toBe("USD 0.10");
    expect(money("EUR", 3)).toBe("EUR 3.00");
    expect(money("USD", "n/a")).toBe("USD n/a");
  });
});
