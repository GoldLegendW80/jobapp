/**
 * The words of Your search in numbers: what each group is called, a rate or "Too few to say",
 * and the dated benchmarks. Facts about the person's own rows, never a verdict. The floors are
 * the api's (api/app/opportunities/insights.py; insights.test.ts reads that file).
 */
import { feedSourceName } from "@/features/opportunities/helpers/sources/source-words";

import { ATS_NAMES } from "../api/boards-api";
import { plural } from "./selection";

/** The api's floors: a funnel rate from CLAIM_MIN applications, a band's from BAND_MIN, ... */
export const FLOORS = {
  CLAIM_MIN: 20,
  BAND_MIN: 5,
  GROUP_MIN: 8,
  MEDIAN_MIN: 3,
} as const;

export const TOO_FEW = "Too few to say";

export const BAND_LABELS: Record<string, string> = {
  "80_plus": "Fit 80 and above",
  "60_79": "Fit 60 to 79",
  under_60: "Fit under 60",
  none: "No fit",
};

const KNOWN_NAMES: Record<string, string> = {
  ...ATS_NAMES,
  linkedin: "LinkedIn",
  workday: "Workday",
  google_careers: "Google Careers",
  vie: "Business France VIE",
  welcome_to_the_jungle: "Welcome to the Jungle",
  welcometothejungle: "Welcome to the Jungle",
  indeed: "Indeed",
  freework: "Free-Work",
};

/** The applicant tracking system of an application ("other" is any other site). */
export function atsLabel(key: string): string {
  if (key === "email") return "By e-mail";
  if (key === "other") return "Other sites";
  if (key === "unknown") return "No address";
  return KNOWN_NAMES[key] ?? key;
}

/** Where the posting came from: the kind of its first source. */
export function channelLabel(key: string): string {
  switch (key) {
    case "mail":
      return "Job alert e-mails";
    case "manual":
      return "Added by hand";
    case "import":
      return "Imported";
    case "site":
      return "Company sites";
    case "unknown":
      return "Not recorded";
    default:
      return KNOWN_NAMES[key] ?? feedSourceName(key) ?? key;
  }
}

/** "25%", or "Too few to say" when the api withheld it. */
export const rateText = (rate: number | null): string =>
  rate === null ? TOO_FEW : `${String(rate)}%`;

/** "1 day", "12.5 days". */
export const daysText = (days: number): string =>
  days === 1 ? "1 day" : `${days.toLocaleString("en-GB")} days`;

/** Under 20 applications the funnel says why it shows no rate. */
export function fewApplications(applied: number): string {
  return `Rates show from ${String(FLOORS.CLAIM_MIN)} applications. You have ${plural(applied, "application")} so far.`;
}

/**
 * Candidate side benchmarks of career-ops' templates/benchmarks.yml (MIT, NOTICE), each with its
 * year: directional, mostly from US mass application platforms, never a target.
 */
export const BENCHMARKS = [
  {
    what: "get any answer",
    typical: "about 3%",
    range: "2% to 13%",
    source: "HiringThing and scale.jobs",
    year: 2025,
  },
  {
    what: "lead to an interview",
    typical: "about 3%",
    range: "2% to 4%",
    source: "HiringThing and resutrack",
    year: 2026,
  },
] as const;
