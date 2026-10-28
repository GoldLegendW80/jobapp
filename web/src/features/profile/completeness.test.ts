/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { completenessOf, roundHalfEven } from "./completeness";

// Expected values computed with api/app/profile/schemas.py `completeness` (the judge).
const owner = JSON.parse(
  readFileSync(
    path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../api/tests/profile/fixtures/owner_export.json",
    ),
    "utf8",
  ),
) as { sections: Record<string, unknown> };

const ALL_MISSING = {
  identity: [
    "full_name",
    "headline",
    "email",
    "phone",
    "city",
    "country",
    "languages",
  ],
  experience: ["employers", "education"],
  facts: ["blocks"],
  skills: ["years_experience", "have"],
  search: [
    "title_include",
    "location",
    "contract_types",
    "remote_stance",
    "interactive_sites",
  ],
  answers: [
    "work_authorization",
    "notice_period",
    "earliest_start",
    "salary_policy",
    "motivation",
  ],
};

describe("roundHalfEven, as Python's round", () => {
  it.each([
    [0.5, 0],
    [1.5, 2],
    [2.5, 2],
    [28.5, 28],
    [15.5, 16],
    [14.2857, 14],
    [83.3333, 83],
    [0.6, 1],
    [99.5, 100],
    [100, 100],
  ])("%f rounds to %i", (value, expected) => {
    expect(roundHalfEven(value)).toBe(expected);
  });
});

describe("completenessOf matches the api", () => {
  it("Harald's real import is 100% everywhere but Search, which needs a ticked job website", () => {
    const result = completenessOf(owner.sections);
    // The engine files hold boards and no job website: searches run the websites alone.
    expect(result.overall).toBe(97);
    for (const [name, section] of Object.entries(result.sections))
      expect(section).toEqual(
        name === "search"
          ? { percent: 80, missing: ["interactive_sites"] }
          : { percent: 100, missing: [] },
      );
  });

  it("an empty profile is 0% with every required field missing", () => {
    const result = completenessOf({});
    expect(result.overall).toBe(0);
    for (const [name, missing] of Object.entries(ALL_MISSING)) {
      expect(result.sections[name as keyof typeof ALL_MISSING]).toEqual({
        percent: 0,
        missing,
      });
    }
  });

  it("rounds a mean of 28.5 down to 28, as Python does", () => {
    const result = completenessOf({
      identity: {
        full_name: "A",
        headline: "B",
        email: "a@example.org",
        phone: "+33 6 00 00 00 00",
        city: "Lille",
      },
      facts: { blocks: [{ title: "T", body: "b" }] },
    });
    expect(result.sections.identity).toEqual({
      percent: 71,
      missing: ["country", "languages"],
    });
    expect(result.sections.facts.percent).toBe(100);
    expect(result.overall).toBe(28);
  });

  it("rounds a mean of 15.5 up to 16", () => {
    const result = completenessOf({
      identity: { full_name: "A", headline: "B", email: "a@example.org" },
      experience: {
        employers: [
          { key: "a", organisation: "A", title: "T", start: "2020-01" },
        ],
      },
    });
    expect(result.sections.identity.percent).toBe(43);
    expect(result.sections.experience).toEqual({
      percent: 50,
      missing: ["education"],
    });
    expect(result.overall).toBe(16);
  });

  it("needs a ticked job website, never a board, and counts 0 years as filled", () => {
    const result = completenessOf({
      search: {
        title_include: { terms: ["x"] },
        location: { terms: ["Paris"] },
        contract_types: ["CDI"],
        remote_stance: "any",
        // A saved board, even on, no longer counts: searches run the websites alone.
        boards: [{ company: "A", ats: "ashby", org: "a", enabled: true }],
        interactive_sites: [
          {
            platform: "custom",
            url: "https://jobs.example.org/",
            enabled: false,
            mode: "llm_browser",
          },
        ],
      },
      skills: { years_experience: 0 },
    });
    expect(result.sections.search).toEqual({
      percent: 80,
      missing: ["interactive_sites"],
    });
    expect(result.sections.skills).toEqual({ percent: 50, missing: ["have"] });
    expect(result.overall).toBe(22);
  });

  it("treats blank strings, null, wrong shapes and a non object section as empty", () => {
    const result = completenessOf({
      identity: { full_name: "   ", headline: null, languages: {} },
      search: { title_include: null, boards: "x" },
      answers: [] as unknown,
    });
    expect(result.sections.identity.missing).toContain("full_name");
    expect(result.sections.identity.missing).toContain("languages");
    expect(result.sections.search.percent).toBe(0);
    expect(result.sections.answers.percent).toBe(0);
  });
});
