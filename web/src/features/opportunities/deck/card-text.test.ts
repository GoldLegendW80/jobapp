import { describe, expect, it } from "vitest";

import {
  LIKED,
  MODE_WORDS,
  SWIPE_TEXT,
  cardLabel,
  contractLine,
  placeLine,
  reasonLine,
  sourceLine,
} from "./card-text";
import { makeCard } from "./deck-test-api";

describe("sourceLine", () => {
  it.each([
    ["greenhouse:example", "via Greenhouse"],
    ["ashby:example", "via Ashby"],
    ["lever:example", "via Lever"],
    ["Greenhouse:Example", "via Greenhouse"],
    ["linkedin", "via LinkedIn"],
    ["remoteok", "via Remote OK"],
    ["weworkremotely", "via We Work Remotely"],
    ["hackernews", "via Hacker News"],
    ["examplefeed", "via Examplefeed"],
    ["manual", null],
    ["import", null],
    ["", null],
    [":", null],
    [null, null],
  ])("%s reads %s", (source, expected) => {
    expect(sourceLine(source)).toBe(expected);
  });
});

describe("placeLine", () => {
  it.each([
    ["Lyon, France", null, "Lyon, France"],
    ["", "remote", "Remote"],
    ["Lyon, France", "hybrid", "Lyon, France, Hybrid"],
    ["Remote, Europe", "remote", "Remote, Europe"],
    ["Paris", "unknown", "Paris"],
    ["  ", null, ""],
  ])("%s with %s reads %s", (location, mode, expected) => {
    expect(placeLine({ location, work_mode: mode })).toBe(expected);
  });
});

describe("contractLine", () => {
  it.each([
    ["CDI", "Permanent"],
    ["CDD", "Fixed term"],
    ["VIE", "VIE"],
    ["Stage", "Internship"],
    ["Alternance", "Apprenticeship"],
    ["Freelance", "Freelance mission"],
    ["Other", "Other"],
  ])("%s reads %s", (code, word) => {
    expect(contractLine({ contract_type: code })).toBe(word);
  });
});

describe("reasonLine", () => {
  it("takes the api's sentence", () => {
    expect(reasonLine(makeCard(1))).toBe(
      "Matches: Python, FastAPI. Gap: Kubernetes.",
    );
  });
  it.each([
    [["Python"], "Go", "Matches: Python. Gap: Go."],
    [[], "Go", "Gap: Go."],
    [["Python", "SQL"], null, "Matches: Python, SQL."],
    [[], null, null],
  ])(
    "builds %j with gap %s when the api sends no sentence",
    (matches, gap, expected) => {
      expect(reasonLine({ reason: { matches, gap, text: null } })).toBe(
        expected,
      );
    },
  );
});

describe("the words", () => {
  it("say the card in one line for a screen reader", () => {
    expect(cardLabel(makeCard(1))).toBe(
      "Example Role 1 at Example Robotics 1, Lyon, France, Permanent, Fit 89",
    );
    expect(cardLabel(makeCard(2, { fit: null }))).toContain("Not scored yet");
  });

  it("use no dash as a separator", () => {
    const all = [
      ...Object.values(LIKED),
      ...Object.values(MODE_WORDS),
      ...Object.values(SWIPE_TEXT),
    ];
    for (const text of all) {
      expect(text).not.toMatch(/[–—]| - /);
    }
  });
});
