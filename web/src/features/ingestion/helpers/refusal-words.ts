/**
 * Why proposals were not kept, in the person's words: one line per section and rule, "14 skills
 * were not kept: the assistant added a field this part of your profile does not have." The rule
 * codes are the server's (api/app/ingestion/refusals.py); the sentence the assistant was given
 * is never shown, it is written for the model.
 */
import { tokens } from "../api/runs-api";
import type { Refused } from "../api/refused-schema";

const NOUNS: Record<string, readonly [string, string]> = {
  identity: ["detail about you", "details about you"],
  experience: ["job or school", "jobs and schools"],
  facts: ["fact", "facts"],
  skills: ["skill", "skills"],
  search: ["search preference", "search preferences"],
  answers: ["application answer", "application answers"],
  preferences: ["setting", "settings"],
};
const ITEM = ["item", "items"] as const;

const QUOTE_NOT_FOUND =
  "the passage the assistant quoted is not written that way in your document.";

const REASONS: Record<string, string> = {
  invalid_item: "the assistant did not write it as a complete item.",
  unknown_section:
    "the assistant named a part of your profile that does not exist.",
  unknown_key:
    "the assistant named a part of your profile that does not exist.",
  unknown_action: "the assistant asked for a change the app does not make.",
  unknown_field:
    "the assistant added a field this part of your profile does not have.",
  invalid_value: "it does not fit the field it was meant for.",
  value_empty: "the assistant left it empty.",
  value_not_text: "the assistant did not write it as text.",
  value_too_large: "it was too long to check.",
  value_not_expected: "a removal came with a value.",
  removal_of_a_list:
    "only you can remove an entry or a list from your profile.",
  setting: "it is a setting that only you choose.",
  no_citation: "the assistant did not quote where your document says it.",
  too_many_citations: "the assistant quoted too many passages for one item.",
  excerpt_not_text: QUOTE_NOT_FOUND,
  excerpt_not_found: QUOTE_NOT_FOUND,
  excerpt_too_short:
    "the passage the assistant quoted was too short to be found in your document.",
  excerpt_too_long: "the passage the assistant quoted was too long.",
  excerpt_too_thin:
    "the passage the assistant quoted held too few letters to be found in your document.",
  excerpt_not_whole_document:
    "your document is very short and the assistant did not quote all of it.",
  span_too_long:
    "the passage the assistant quoted covers too much of your document.",
  document_not_text: "your document had no text yet.",
  document_unreadable: "the text of your document could not be read.",
  address_not_in_source:
    "the address is not written that way in the passage quoted.",
  name_not_in_source: "the name is not written that way in the passage quoted.",
  date_format: "a date was not written as a year, or a year and a month.",
  year_not_in_source: "a year is not written in the passage quoted.",
  month_not_in_source:
    "a month is not written next to its year in the passage quoted.",
  month_apart_from_year:
    "a month and its year are not on the same line of your document.",
  two_digit_year:
    "your document writes a year with two digits, and the app does not guess the century.",
  phone_too_short: "the phone number has too few digits.",
  phone_not_in_source:
    "the phone number is not written that way in the passage quoted.",
  number_not_in_source: "a number in it is not written in the passage quoted.",
  years_not_in_source:
    "the years of experience are not written in the passage quoted.",
};
const OTHER = "it did not pass the checks against your document.";

/** Why a proposal refused for `rule` was not kept, as a person reads it. */
export const refusalReason = (rule: string) => REASONS[rule] ?? OTHER;

/** "14 skills were not kept: <why>", "1 fact was not kept: <why>". */
export function refusalLine(refused: Refused): string {
  const [one, many] = NOUNS[refused.section] ?? ITEM;
  const noun = refused.count === 1 ? `${one} was` : `${many} were`;
  return `${tokens(refused.count)} ${noun} not kept: ${refusalReason(refused.rule)}`;
}
