/**
 * Jobs J30 (owner, 2026-11-26, "Garder avec un badge"): what of a posting is outside the criteria
 * of the search that found it, in words. The api computes the codes (`outside_criteria`, in the
 * order contract, work_mode, older, language; api/app/jobs/criteria_badge.py); this names the
 * posting's own contract and work mode, the age window of the search, and (jobs J35) the languages
 * it requires that the person's profile does not list.
 */
import { contractWord, WORK_MODE_WORDS } from "@/lib/wording";

/** The Criteria filter of the Jobs list (api `criteria`); "all", the default, is no filter. */
export const CRITERIA_CHOICES = ["within", "outside"] as const;
export type CriteriaChoice = (typeof CRITERIA_CHOICES)[number];
export const CRITERIA_CHOICE_LABELS: Record<CriteriaChoice, string> = {
  within: "Within your criteria",
  outside: "Outside your criteria",
};

/** A search's window when it saved none (the api's SearchSection default). */
export const DEFAULT_WINDOW_DAYS = 30;

// The contract words of a posting as the middle of a sentence ("freelance mission").
const CONTRACT_PHRASES: Record<string, string> = {
  CDI: "permanent contract",
  CDD: "fixed term contract",
};

export interface CriteriaFacts {
  outside_criteria?: readonly string[] | null;
  contract_type: string;
  work_mode?: string | null;
  published_on: string | null;
  found_on: string;
  /** Jobs J35: the required languages the profile does not list, as the api names them. */
  languages_missing?: readonly string[] | null;
}

function contractPhrase(code: string): string {
  const word = CONTRACT_PHRASES[code] ?? contractWord(code);
  return word === code ? word : word.toLowerCase();
}

function daysBetween(from: string, to: string): number | null {
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.round((end - start) / 86_400_000);
}

function olderPhrase(job: CriteriaFacts, windowDays: number | null): string {
  if (windowDays !== null)
    return `older than ${windowDays === 1 ? "1 day" : `${String(windowDays)} days`}`;
  const age =
    job.published_on === null
      ? null
      : daysBetween(job.published_on, job.found_on);
  return age !== null && age > 0
    ? `published ${String(age)} days before it was found`
    : "older than your search's window";
}

function joinAnd(names: readonly string[]): string {
  if (names.length < 2) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1] ?? ""}`;
}

/**
 * Jobs J35: "Requires German, which your profile does not list", or null when the badge has no
 * `language` code. A badge the api has not followed yet with a name still reads as a sentence.
 */
export function languageWords(job: CriteriaFacts): string | null {
  if (!(job.outside_criteria ?? []).includes("language")) return null;
  const missing = [...new Set(job.languages_missing ?? [])];
  return missing.length
    ? `Requires ${joinAnd(missing)}, which your profile does not list`
    : "Requires a language your profile does not list";
}

/**
 * Each part of the badge's criteria, in the api's order, the language aside (`languageWords`); a
 * code this build does not know is still said.
 */
export function outsideParts(
  job: CriteriaFacts,
  windowDays: number | null,
): string[] {
  const codes = (job.outside_criteria ?? []).filter(
    (code) => code !== "language",
  );
  return codes.map((code) => {
    if (code === "contract") return contractPhrase(job.contract_type);
    if (code === "work_mode") {
      const mode = job.work_mode ? WORK_MODE_WORDS[job.work_mode] : undefined;
      return mode ? mode.toLowerCase() : "its work mode";
    }
    if (code === "older") return olderPhrase(job, windowDays);
    return "another criterion of your search";
  });
}

/**
 * "Outside your criteria: freelance mission, remote, older than 30 days", then the languages
 * ("Requires German, which your profile does not list"), or null within.
 */
export function outsideCriteriaWords(
  job: CriteriaFacts,
  windowDays: number | null,
): string | null {
  const parts = [...new Set(outsideParts(job, windowDays))];
  const criteria = parts.length
    ? `Outside your criteria: ${parts.join(", ")}`
    : null;
  const languages = languageWords(job);
  return criteria && languages
    ? `${criteria}. ${languages}`
    : (criteria ?? languages);
}

/**
 * The age window of the search that found a posting, when every search of the person has the same
 * one (the api serves no posting's search): each search's `days`, else DEFAULT_WINDOW_DAYS. Null
 * when they differ or none is known.
 */
export function sharedWindow(
  searches: readonly { criteria: { days?: number | null } }[] | undefined,
): number | null {
  const windows = new Set(
    (searches ?? []).map(
      (search) => search.criteria.days ?? DEFAULT_WINDOW_DAYS,
    ),
  );
  const [only] = windows;
  return windows.size === 1 && only !== undefined ? only : null;
}
