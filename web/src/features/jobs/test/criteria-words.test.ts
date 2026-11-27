import { describe, expect, it } from "vitest";

import {
  outsideCriteriaWords,
  sharedWindow,
  type CriteriaFacts,
} from "../helpers/criteria-words";

const SEPARATOR = /[–—]| - /;

const posting = (overrides: Partial<CriteriaFacts> = {}): CriteriaFacts => ({
  outside_criteria: [],
  contract_type: "CDI",
  work_mode: null,
  published_on: null,
  found_on: "2026-11-26",
  ...overrides,
});

describe("the badge of a posting outside its search's criteria (J30)", () => {
  it("names the posting's own contract, work mode and the search's window", () => {
    const words = outsideCriteriaWords(
      posting({
        outside_criteria: ["contract", "work_mode", "older"],
        contract_type: "Freelance",
        work_mode: "remote",
        published_on: "2026-10-01",
      }),
      30,
    );
    expect(words).toBe(
      "Outside your criteria: freelance mission, remote, older than 30 days",
    );
    expect(words).not.toMatch(SEPARATOR);
  });

  it.each([
    ["CDI", "permanent contract"],
    ["CDD", "fixed term contract"],
    ["Stage", "internship"],
    ["Alternance", "apprenticeship"],
    ["VIE", "VIE"],
    ["Interim", "Interim"],
  ])("says the contract %s as %s", (contract, said) => {
    expect(
      outsideCriteriaWords(
        posting({ outside_criteria: ["contract"], contract_type: contract }),
        30,
      ),
    ).toBe(`Outside your criteria: ${said}`);
  });

  it.each([
    ["remote", "remote"],
    ["hybrid", "hybrid"],
    ["office", "on site"],
    [null, "its work mode"],
  ])("says the work mode %s as %s", (mode, said) => {
    expect(
      outsideCriteriaWords(
        posting({ outside_criteria: ["work_mode"], work_mode: mode }),
        30,
      ),
    ).toBe(`Outside your criteria: ${said}`);
  });

  it("says the posting's own age when the window of its search is not known", () => {
    const older = posting({
      outside_criteria: ["older"],
      published_on: "2026-10-12",
    });
    expect(outsideCriteriaWords(older, null)).toBe(
      "Outside your criteria: published 45 days before it was found",
    );
    expect(outsideCriteriaWords(older, 1)).toBe(
      "Outside your criteria: older than 1 day",
    );
    expect(
      outsideCriteriaWords(posting({ outside_criteria: ["older"] }), null),
    ).toBe("Outside your criteria: older than your search's window");
  });

  it("says a code this build does not know, never nothing", () => {
    expect(
      outsideCriteriaWords(posting({ outside_criteria: ["salary"] }), 30),
    ).toBe("Outside your criteria: another criterion of your search");
  });

  it.each([[[]], [null], [undefined]])(
    "shows no badge for a posting within them (%j)",
    (codes) => {
      expect(
        outsideCriteriaWords(posting({ outside_criteria: codes }), 30),
      ).toBeNull();
    },
  );

  it("reads the window every search shares, else none", () => {
    expect(sharedWindow([{ criteria: {} }])).toBe(30);
    expect(
      sharedWindow([{ criteria: { days: 14 } }, { criteria: { days: 14 } }]),
    ).toBe(14);
    expect(sharedWindow([{ criteria: { days: 30 } }, { criteria: {} }])).toBe(
      30,
    );
    expect(
      sharedWindow([{ criteria: { days: 14 } }, { criteria: {} }]),
    ).toBeNull();
    expect(sharedWindow([])).toBeNull();
    expect(sharedWindow(undefined)).toBeNull();
  });
});

describe("the badge of a posting that requires a language the profile does not list (J35)", () => {
  it("says the language alone", () => {
    expect(
      outsideCriteriaWords(
        posting({
          outside_criteria: ["language"],
          languages_missing: ["German"],
        }),
        30,
      ),
    ).toBe("Requires German, which your profile does not list");
  });

  it("joins several languages and an alternative, after the other criteria", () => {
    const words = outsideCriteriaWords(
      posting({
        outside_criteria: ["contract", "language"],
        contract_type: "Freelance",
        languages_missing: ["German", "Italian or Spanish", "German"],
      }),
      30,
    );
    expect(words).toBe(
      "Outside your criteria: freelance mission. Requires German and Italian or Spanish, which your profile does not list",
    );
    expect(words).not.toMatch(SEPARATOR);
    expect(words).not.toContain("another criterion");
  });

  it("still reads as a sentence when the api names no language", () => {
    expect(
      outsideCriteriaWords(posting({ outside_criteria: ["language"] }), 30),
    ).toBe("Requires a language your profile does not list");
  });

  it("says nothing of the languages without the code", () => {
    expect(
      outsideCriteriaWords(posting({ languages_missing: ["German"] }), 30),
    ).toBeNull();
  });
});
