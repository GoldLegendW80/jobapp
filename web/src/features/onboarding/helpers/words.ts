import {
  draftOfSearch,
  rowsOf,
  sourceCount,
} from "@/features/jobs/helpers/sources";
import { type Answers, type Search } from "@/features/profile/api/profile-api";
import { salaryAnswered } from "@/features/profile/helpers/completeness";
import { type ApplySettings } from "@/features/apply/api/settings-api";
import type { ModelCredential } from "@/features/agents/api/agents-api";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";
import {
  COMPANY_WORDS,
  LANGUAGE_WORDS,
  REMOTE_WORDS,
  SENIORITY_WORDS,
  TONE_WORDS,
  WIZARD_STEP_WORDS,
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

import {
  splitTerms,
  type AuthorizationDraft,
  type Draft,
  type PayDraft,
  type StepId,
} from "./steps";

/** The welcome page's one line under its title. */
export const WELCOME_LINE =
  "A few short answers, and the app looks for postings that fit. You can change every answer later in your profile.";

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

/** What the summary says of an answer for forms the person left empty. */
export const NOT_GIVEN = "not given yet";

function authorizationLine({
  country,
  allowed,
  sponsorship,
  kept,
}: AuthorizationDraft): string {
  const place = country.trim();
  if (!place) return `${WORDING.workAuthorisation}: ${NOT_GIVEN}`;
  const may =
    allowed === "yes"
      ? "you may work there"
      : allowed === "no"
        ? "you may not work there"
        : kept;
  const visa =
    sponsorship === "yes"
      ? "you need a visa sponsorship"
      : sponsorship === "no"
        ? "no visa sponsorship needed"
        : "";
  return `${WORDING.workAuthorisation}: ${[place, may, visa].filter(Boolean).join(", ")}`;
}

/**
 * The answers for forms the wizard asks and the person left empty, in the summary's words: what
 * the profile's completeness still counts as missing among them.
 */
export function answersLeft(draft: Draft, answers: Answers): string[] {
  return [
    [WORDING.whyLooking, draft.motivation],
    [WORDING.noticePeriod, draft.notice],
    [WORDING.earliestStart, draft.start],
    [WORDING.workAuthorisation, draft.authorization.country],
    [WIZARD_STEP_WORDS.pay, salaryAnswered({ ...answers }) ? "answered" : ""],
  ]
    .filter(([, value]) => !value?.trim())
    .map(([name]) => (name ?? "").toLowerCase());
}

/**
 * The answers in plain words, one row per step with a line per answer, each row with its
 * "Change". Building your profile has none: what it found is in the profile, and in the rows of
 * the steps after it.
 */
export function summaryRows(
  draft: Draft,
  settings: ApplySettings | undefined,
  search: Search,
  credential: ModelCredential | null | undefined,
): Record<Exclude<StepId, "summary" | "building">, string[]> {
  const pays = [payLine(draft.salary), payLine(draft.daily)];
  const fullAuto =
    settings?.mode_after_like === "full_auto" && draft.mode === null;
  const review = draft.review ?? settings?.review_before_send ?? false;
  const said = (name: string, value: string) =>
    `${name}: ${value.trim() || NOT_GIVEN}`;
  return {
    access: [
      credential
        ? `${KIND_LABEL[credential.kind]} ending in ${credential.last4}`
        : "None saved yet: reading, writing and searching wait for it",
    ],
    profile: ["Your documents and proposals are on the Fill my profile page."],
    looking: [
      [
        listOr(splitTerms(draft.roles), "No role yet"),
        draft.seniority.map((s) => SENIORITY_WORDS[s]).join(", "),
        splitTerms(draft.industries).join(", "),
        draft.companyTypes.map((c) => COMPANY_WORDS[c]).join(", "),
      ]
        .filter(Boolean)
        .join("; "),
      said(WORDING.whyLooking, draft.motivation),
    ],
    where: [
      `${draft.anywhere ? "Anywhere" : listOr(splitTerms(draft.locations), "No place yet")}; ${
        draft.remote ? REMOTE_WORDS[draft.remote] : "no preference given"
      }`,
    ],
    sites: [
      listOr(
        rowsOf(draftOfSearch(search))
          .filter((row) => row.enabled)
          .map((row) => row.name),
        "Nothing yet",
      ),
    ],
    work: [
      listOr(draft.contracts.map(contractWord), "Any contract"),
      `${said(WORDING.noticePeriod, draft.notice)}; ${said(
        WORDING.earliestStart.toLowerCase(),
        draft.start,
      )}`,
      authorizationLine(draft.authorization),
    ],
    pay: [
      draft.ratherNotSay
        ? "Rather not say"
        : listOr(
            [
              pays[0] ? `Salary ${pays[0]}` : "",
              pays[1] ? `Daily rate ${pays[1]}` : "",
            ].filter(Boolean),
            "Not given",
          ),
    ],
    // Both choices of the step: the mode after a Like, and what the job page's Apply does.
    apply: [
      `${fullAuto ? WORDING.fullAuto : WORDING.reviewFirst} after a Like; ${
        review
          ? "Apply waits for you to review each filled form"
          : "Apply sends each filled form for you"
      }; documents in ${LANGUAGE_WORDS[draft.language].toLowerCase()}, ${TONE_WORDS[
        draft.tone
      ].toLowerCase()} tone`,
    ],
  };
}

/**
 * What Building your profile says of the proposals left for Fill my profile: `waiting` of them,
 * `changing` of which would change a value the profile holds; the others need a look first (a
 * line that reads like an instruction, a transcription, a section that could not be written).
 */
export function waitingWords(
  waiting: number,
  changing: number,
): { lead: string; reason: string } {
  const one = waiting === 1;
  const lead = `${waiting} more ${one ? "waits" : "wait"} for you`;
  if (changing >= waiting)
    return {
      lead,
      reason: `${one ? "it" : "they"} would change a value you already have`,
    };
  if (changing === 0)
    return { lead, reason: `${one ? "it needs" : "they need"} a look first` };
  return {
    lead,
    reason: `${changing} would change a value you already have, the others need a look first`,
  };
}

/** How many places the first search reads: the websites, job feeds and company boards of the
 * list (jobs/sources.ts). */
export function sweepCoverage(search: Search): number {
  return sourceCount(draftOfSearch(search));
}
