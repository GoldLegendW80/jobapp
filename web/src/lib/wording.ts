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
  deckEmptyHint: "Find new postings, or lower the threshold in Search options.",
} as const;

/** The empty deck line; it names the fit threshold in force so the number is never a mystery. */
export function deckEmpty(threshold: number): string {
  return `Nothing new above your threshold of ${threshold}.`;
}
