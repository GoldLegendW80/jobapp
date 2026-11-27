/**
 * Jobs J30 (owner, 2026-11-26, "Garder avec un badge"): what of a posting is outside the criteria
 * of the search that found it, in words. The api computes the codes (`outside_criteria`, in the
 * order contract, work_mode, older; api/app/jobs/criteria_badge.py); this names the posting's own
 * contract and work mode, and the age window of the search.
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

/** Each part of the badge, in the api's order; a code this build does not know is still said. */
export function outsideParts(
  job: CriteriaFacts,
  windowDays: number | null,
): string[] {
  return (job.outside_criteria ?? []).map((code) => {
    if (code === "contract") return contractPhrase(job.contract_type);
    if (code === "work_mode") {
      const mode = job.work_mode ? WORK_MODE_WORDS[job.work_mode] : undefined;
      return mode ? mode.toLowerCase() : "its work mode";
    }
    if (code === "older") return olderPhrase(job, windowDays);
    return "another criterion of your search";
  });
}

/** "Outside your criteria: freelance mission, remote, older than 30 days", or null within. */
export function outsideCriteriaWords(
  job: CriteriaFacts,
  windowDays: number | null,
): string | null {
  const parts = [...new Set(outsideParts(job, windowDays))];
  return parts.length ? `Outside your criteria: ${parts.join(", ")}` : null;
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
