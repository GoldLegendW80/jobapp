/**
 * The plain words of the app: one place for the labels people read, so no page shows jargon
 * ("ATS", "sweep", "lease") or a raw code ("CDI"). Pages import from here instead of writing the
 * words again; a missing word is a one line addition. No dash is used as a separator in any value.
 */

/** Contract codes of a posting, as words. A code this build does not know is shown as it came. */
export const CONTRACT_WORDS: Record<string, string> = {
  CDI: "Permanent",
  CDD: "Fixed term",
  VIE: "VIE",
  Stage: "Internship",
  Alternance: "Apprenticeship",
  Freelance: "Freelance mission",
};

export function contractWord(code: string): string {
  return CONTRACT_WORDS[code] ?? code;
}

/** Where a posting's work is done (opportunities O3), as words. */
export const WORK_MODE_WORDS: Record<string, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "On site",
};

/** What a posting's pay is counted per, as the end of a sentence ("a year"). */
export const PAY_PERIOD_WORDS: Record<string, string> = {
  year: "a year",
  month: "a month",
  day: "a day",
  hour: "an hour",
};

export const WORDING = {
  advanced: "Advanced",
  applicationSite: "Application site",
  findNewPostings: "Find new postings",
  rolesToNeverApplyFor: "Roles to never apply for",
  fitScore: "Fit score",
  // The three thresholds, each card names its own so nobody confuses them.
  showPostingsFrom: "Show postings from",
  writeDocumentsFrom: "Write documents from",
  applyAutomaticallyFrom: "Apply automatically from",
  // The deck.
  like: "Like",
  pass: "Pass",
  undoLast: "Undo last",
  reviewFirst: "Review first",
  fullAuto: "Full auto",
  searchOptions: "Search options",
  deckEmptyHint: "Find new postings, or lower the threshold in Search options.",
  // Opportunities O3.
  pay: "Pay",
  workMode: "Work mode",
  notStated: "Not stated",
} as const;

/** The empty deck line; it names the fit threshold in force so the number is never a mystery. */
export function deckEmpty(threshold: number): string {
  return `Nothing new above your threshold of ${threshold}.`;
}
