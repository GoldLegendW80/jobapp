import { describe, expect, it } from "vitest";

import { refusalLine, refusalReason } from "../helpers/refusal-words";

/** Every rule the server may count (api/app/ingestion/refusals.py, items.py, citations.py). */
const SERVER_RULES = [
  "invalid_item",
  "removal_of_a_list",
  "invalid_value",
  "unknown_section",
  "unknown_key",
  "unknown_action",
  "setting",
  "unknown_field",
  "value_too_large",
  "value_empty",
  "value_not_expected",
  "value_not_text",
  "document_not_text",
  "document_unreadable",
  "no_citation",
  "too_many_citations",
  "excerpt_not_text",
  "excerpt_too_short",
  "excerpt_too_long",
  "excerpt_too_thin",
  "excerpt_not_whole_document",
  "excerpt_not_found",
  "span_too_long",
  "address_not_in_source",
  "name_not_in_source",
  "date_format",
  "year_not_in_source",
  "month_not_in_source",
  "month_apart_from_year",
  "two_digit_year",
  "phone_too_short",
  "phone_not_in_source",
  "number_not_in_source",
  "years_not_in_source",
];
const FALLBACK = refusalReason("a_rule_of_a_newer_server");

const line = (section: string, rule: string, count: number) =>
  refusalLine({ section, rule, count, sentence: "for the assistant" });

describe("refusal words", () => {
  it.each(SERVER_RULES)("%s reads as a plain sentence of its own", (rule) => {
    const reason = refusalReason(rule);
    expect(reason).not.toBe(FALLBACK);
    expect(reason).toMatch(/^[a-z].*\.$/);
    expect(reason).not.toMatch(/[–—]| - |_/);
  });

  it("names the section, counts in words a person reads, and says was or were", () => {
    expect(line("skills", "unknown_field", 14)).toBe(
      "14 skills were not kept: the assistant added a field this part of your profile does not have.",
    );
    expect(line("skills", "excerpt_not_found", 1)).toBe(
      "1 skill was not kept: the passage the assistant quoted is not written that way in your document.",
    );
    expect(line("experience", "two_digit_year", 2)).toMatch(
      /^2 jobs and schools were not kept: /,
    );
    expect(line("identity", "setting", 1)).toBe(
      "1 detail about you was not kept: it is a setting that only you choose.",
    );
    expect(line("facts", "excerpt_too_short", 1_204)).toMatch(
      /^1,204 facts were not kept: /,
    );
    expect(line("search", "invalid_value", 3)).toMatch(
      /^3 search preferences were not kept: /,
    );
    expect(line("answers", "invalid_value", 1)).toMatch(
      /^1 application answer was not kept: /,
    );
  });

  it("a section or a rule it does not know still reads as a sentence", () => {
    expect(line("a_new_section", "a_new_rule", 2)).toBe(
      "2 items were not kept: it did not pass the checks against your document.",
    );
    expect(line("skills", "a_new_rule", 1)).not.toMatch(/a_new_rule|_/);
  });
});
