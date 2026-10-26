import { type Search } from "@/features/profile/api";
import { type ApplySettings } from "@/features/apply/settings-api";
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
): Record<Exclude<StepId, "summary">, string> {
  const pays = [payLine(draft.salary), payLine(draft.daily)];
  const fullAuto =
    settings?.mode_after_like === "full_auto" && draft.mode === null;
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
    apply: `${fullAuto ? WORDING.fullAuto : WORDING.reviewFirst}; documents in ${LANGUAGE_WORDS[
      draft.language
    ].toLowerCase()}, ${TONE_WORDS[draft.tone].toLowerCase()} tone`,
  };
}

export function sweepCoverage(search: Search): string {
  const boards = search.boards.filter((board) => board.enabled).length;
  const sources = (search.browser_sources ?? []).filter(
    (source) => source.enabled,
  ).length;
  const parts = [
    `${boards} ${boards === 1 ? "board" : "boards"}`,
    ...(sources ? [`${sources} ${sources === 1 ? "site" : "sites"}`] : []),
  ];
  return parts.join(" and ");
}
