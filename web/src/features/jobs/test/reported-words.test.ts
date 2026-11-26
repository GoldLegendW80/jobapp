import { describe, expect, it } from "vitest";

import {
  hasReportedFigures,
  keptParts,
  refusedSentence,
  reportedSentences,
  REFUSAL_REASONS,
} from "../helpers/reported-words";

const SEPARATOR = /[–—]| - /;

describe("what a source of a search did with what it reported (J30)", () => {
  it("says every figure in sentences, the assistant's count as its own", () => {
    const said = reportedSentences({
      found: 7,
      new: 4,
      outside: 1,
      duplicates: 3,
      skipped_by_rule: 1,
      unwanted: 2,
      refused: { title_not_on_page: 2, page_error: 1 },
      opened_by_app: 2,
      results_read: 1490,
    });
    expect(said).toBe(
      "7 postings found: 4 new postings kept, 1 of them outside your criteria; 3 already in your list; 1 set to Skipped by a never apply rule. " +
        "2 postings set aside by your unwanted titles and places. " +
        "3 refused by the app's check: 2 because its title was not on its page; 1 because its page answered an error. " +
        "The app opened 2 postings itself, as the assistant had not. " +
        "The assistant says it read 1,490 results.",
    );
    expect(said).not.toMatch(SEPARATOR);
  });

  it("leaves out a figure that is zero or absent", () => {
    expect(
      reportedSentences({
        found: 2,
        new: 2,
        outside: 0,
        duplicates: 0,
        unwanted: 0,
        refused: { not_seen: 0 },
        opened_by_app: 0,
        results_read: null,
      }),
    ).toBe("2 postings found: 2 new postings kept.");
    expect(reportedSentences({ found: 0, new: 0, results_read: 1490 })).toBe(
      "No posting kept. The assistant says it read 1,490 results.",
    );
  });

  it("says an older sweep's postings of another contract", () => {
    expect(reportedSentences({ found: 1, new: 1, other_contract: 3 })).toBe(
      "1 posting found: 1 new posting kept. 3 postings of a contract you did not choose left out.",
    );
  });

  it.each(Object.entries(REFUSAL_REASONS))(
    "gives the verification's %s a sentence",
    (reason, words) => {
      const said = refusedSentence({ [reason]: 1 });
      expect(said).toBe(`1 refused by the app's check: 1 because ${words}.`);
      expect(said).not.toContain(reason);
      expect(said).not.toMatch(SEPARATOR);
    },
  );

  it("gives a reason it does not know one generic sentence, never the code", () => {
    const said = refusedSentence({ salary_not_on_page: 2 });
    expect(said).toBe(
      "2 refused by the app's check: 2 because the app's check found another problem.",
    );
    expect(said).not.toContain("salary_not_on_page");
  });

  it("knows whether a source has a figure of J30, else it reads as before", () => {
    expect(hasReportedFigures({ found: 3, new: 2 })).toBe(false);
    expect(
      hasReportedFigures({
        duplicates: 0,
        outside: 0,
        unwanted: 0,
        refused: {},
        opened_by_app: 0,
        results_read: null,
        other_contract: 0,
      }),
    ).toBe(false);
    for (const figures of [
      { duplicates: 1 },
      { outside: 1 },
      { unwanted: 1 },
      { refused: { page_error: 1 } },
      { opened_by_app: 1 },
      { results_read: 12 },
      { other_contract: 1 },
    ])
      expect(hasReportedFigures(figures)).toBe(true);
  });

  it("names the kept postings of a whole search", () => {
    expect(keptParts({ new: 2, outside: 1, duplicates: 3 })).toEqual([
      "2 new postings kept, 1 of them outside your criteria",
      "3 already in your list",
    ]);
    expect(keptParts({ new: 0, duplicates: 0 })).toEqual([]);
  });
});
