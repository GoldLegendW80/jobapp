import {
  draftOfSearch,
  rowsOf,
  sourceCount,
} from "@/features/jobs/helpers/sources";
import { type Search } from "@/features/profile/api/profile-api";
import { type ApplySettings } from "@/features/apply/api/settings-api";
import {
  COMPANY_WORDS,
  LANGUAGE_WORDS,
  REMOTE_WORDS,
  SENIORITY_WORDS,
  TONE_WORDS,
  contractWord,
  WORDING,
} from "@/lib/wording";

export {
  COMPANY_WORDS,
  LANGUAGE_WORDS,
  REMOTE_WORDS,
  SENIORITY_WORDS,
  TONE_WORDS,
} from "@/lib/wording";

import { splitTerms, type Draft, type PayDraft, type StepId } from "./steps";

/** What "Start" says once the first search was asked for, by the api's answer. */
export const SWEEP_SENTENCE = {
  started:
    "Your first search has started. New postings appear as it finds them.",
  running: "A search was already running: it is your first one.",
  no_websites:
    "Your list of websites is empty. Add a few, then start your first search.",
  search_invalid:
    "Open Search options and save them again, then find new postings.",
  queue_down:
    "The search could not start. Find new postings again in a minute.",
} as const;

const listOr = (items: string[], none: string) =>
  items.length ? items.join(", ") : none;

function payLine(pay: PayDraft): string | null {
  return pay.min.trim() && pay.max.trim()
    ? `${pay.min.trim()} to ${pay.max.trim()} ${pay.currency}`
    : null;
}

/** The answers in plain words, one row per step, each with its "Change". */
export function summaryRows(
  draft: Draft,
  settings: ApplySettings | undefined,
  search: Search,
): Record<Exclude<StepId, "summary">, string> {
  const pays = [payLine(draft.salary), payLine(draft.daily)];
  const fullAuto =
    settings?.mode_after_like === "full_auto" && draft.mode === null;
  const review = draft.review ?? settings?.review_before_send ?? false;
  return {
    profile: "Your documents and proposals are on the Fill my profile page.",
    looking: [
      listOr(splitTerms(draft.roles), "No role yet"),
      draft.seniority.map((s) => SENIORITY_WORDS[s]).join(", "),
      splitTerms(draft.industries).join(", "),
      draft.companyTypes.map((c) => COMPANY_WORDS[c]).join(", "),
    ]
      .filter(Boolean)
      .join("; "),
    where: `${draft.anywhere ? "Anywhere" : listOr(splitTerms(draft.locations), "No place yet")}; ${
      draft.remote ? REMOTE_WORDS[draft.remote] : "no preference given"
    }`,
    sites: listOr(
      rowsOf(draftOfSearch(search))
        .filter((row) => row.enabled)
        .map((row) => row.name),
      "Nothing yet",
    ),
    work: listOr(draft.contracts.map(contractWord), "Any contract"),
    pay: draft.ratherNotSay
      ? "Rather not say"
      : listOr(
          [
            pays[0] ? `Salary ${pays[0]}` : "",
            pays[1] ? `Daily rate ${pays[1]}` : "",
          ].filter(Boolean),
          "Not given",
        ),
    // Both choices of the step: the mode after a Like, and what the job page's Apply does.
    apply: `${fullAuto ? WORDING.fullAuto : WORDING.reviewFirst} after a Like; ${
      review
        ? "Apply waits for you to review each filled form"
        : "Apply sends each filled form for you"
    }; documents in ${LANGUAGE_WORDS[draft.language].toLowerCase()}, ${TONE_WORDS[
      draft.tone
    ].toLowerCase()} tone`,
  };
}

/** How many places the first search reads: the websites, job feeds and company boards of the
 * list (jobs/sources.ts). */
export function sweepCoverage(search: Search): number {
  return sourceCount(draftOfSearch(search));
}
