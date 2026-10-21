import {
  COMPANY_TYPES,
  DOCUMENTS_LANGUAGES,
  DOCUMENTS_TONES,
  SENIORITIES,
  type Search,
} from "@/features/profile/api";
import { type ApplySettings } from "@/features/apply/settings-api";
import { contractWord, WORDING } from "@/lib/wording";

import { splitTerms, type Draft, type PayDraft, type StepId } from "./steps";

/** The answers of the wizard in plain words: the choices' labels and the summary's rows. */
export const SENIORITY_WORDS: Record<(typeof SENIORITIES)[number], string> = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  lead: "Lead",
  principal: "Principal or staff",
};
export const COMPANY_WORDS: Record<(typeof COMPANY_TYPES)[number], string> = {
  startup: "Startup",
  scaleup: "Scale-up",
  large_company: "Large company",
  public_sector: "Public sector",
  nonprofit: "Nonprofit",
  agency: "Agency",
  consulting: "Consulting firm",
};
export const REMOTE_WORDS = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "On site",
  any: "Any of these",
} as const;
export const LANGUAGE_WORDS: Record<
  (typeof DOCUMENTS_LANGUAGES)[number],
  string
> = {
  auto: "Automatic, from the posting",
  en: "English",
  fr: "French",
};
export const TONE_WORDS: Record<(typeof DOCUMENTS_TONES)[number], string> = {
  plain: "Plain",
  formal: "Formal",
  warm: "Warm",
};

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
