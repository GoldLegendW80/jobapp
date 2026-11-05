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

/** Shared wizard and profile choice labels. Codes stay separate from their plain words. */
export const SENIORITY_WORDS = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  lead: "Lead",
  principal: "Principal or staff",
} as const;
export const COMPANY_WORDS = {
  startup: "Startup",
  scaleup: "Scale-up",
  large_company: "Large company",
  public_sector: "Public sector",
  nonprofit: "Nonprofit",
  agency: "Agency",
  consulting: "Consulting firm",
} as const;
export const REMOTE_WORDS = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "On site",
  any: "Any of these",
} as const;
export const LANGUAGE_WORDS = {
  auto: "Automatic, from the posting",
  en: "English",
  fr: "French",
} as const;
export const TONE_WORDS = {
  plain: "Plain",
  formal: "Formal",
  warm: "Warm",
} as const;
export const WIZARD_STEP_WORDS = {
  profile: "Your profile",
  looking: "What you are looking for",
  where: "Where",
  sites: "Websites to search",
  work: "How you want to work",
  pay: "Pay",
  apply: "How the app applies",
  summary: "Summary",
} as const;

/** The short name of a feed of an old sweep or posting ("Hacker News, freelance thread"). */
export const FEED_SHORT_NAMES = {
  remoteok: "Remote OK",
  weworkremotely: "We Work Remotely",
  hackernews_freelance: "Hacker News, freelance thread",
  hackernews_hiring: "Hacker News, hiring thread",
} as const;

export const WORDING = {
  // O10: shared labels used by the simple product pages.
  discover: "Discover",
  deck: "Deck",
  contractTypes: "Contract types",
  remote: "Remote",
  onSite: "On site",
  remoteOrOnSite: "Remote or on site",
  either: "Either",
  source: "Source",
  anySource: "Any source",
  addedByYou: "Added by you",
  more: "More",
  tryAgain: "Try again",
  openPostingPage: "Open the posting page",
  yourProfile: "Your profile",
  welcome: "Welcome",
  skipForNow: "Skip for now",
  wizard: "Wizard",
  progress: "Progress",
  wizardProgress: "Wizard progress",
  back: "Back",
  next: "Next",
  start: "Start",
  skipThisStep: "Skip this step",
  anywhere: "Anywhere",
  savedPatternHint:
    "Your saved roles use a pattern. View it under Advanced or change it in Search options.",
  roles: "Roles",
  seniority: "Seniority",
  industriesOptional: "Industries (optional)",
  companyTypesOptional: "Company types (optional)",
  places: "Places",
  remoteHybridOnSite: "Remote, hybrid or on site",
  employmentContracts: "Employment contracts (choose several if you like)",
  freelanceContracts: "Freelance work",
  ratherNotSay: "Rather not say",
  yearlySalary: "Yearly salary",
  dailyRateFreelance: "Daily rate (freelance)",
  afterLike: "After a Like",
  documentsLanguage: "Language of your CV and letters",
  tone: "Tone",
  change: "Change",
  readProfile: "Read them into your profile",
  reviewProposals: "Review the proposals",
  importOtherProfile: "Or add a LinkedIn data export (ZIP), or paste a text",
  added: "Added",
  remove: "Remove",
  addAll: "Add all",
  addTicked: "Add the ticked ones",
  findInDiscover: "Find it in Discover",
  read: "Read",
  wordsFor: "Words for",
  on: "On",
  off: "Off",
  termsOf: "Terms of",
  opensNewTab: "(opens in a new tab)",
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
  deckNoPostings: "No postings yet.",
  deckNoPostingsHint:
    "Add a few websites to your list in Find jobs and start a search: what the app finds shows up here.",
  findJobs: "Find jobs",
  addSkills: "Add your skills",
  addModelKey: "Add it in Account, Model access",
  applicationSettings: "Application settings",
  // The posting page: Apply and the documents first, the rest folded.
  yourApplication: "Your application",
  moreAboutPosting: "More about this posting",
  addWebsites: "Add websites",
  // Opportunities O3.
  pay: "Pay",
  // A posting that states no pay, said in a muted style wherever pay is shown.
  payNotGiven: "Pay not given",
  workMode: "Work mode",
  notStated: "Not stated",
  payLowest: "Lowest pay",
  payHighest: "Highest pay",
  payFrom: "From",
  payTo: "To",
  payCurrency: "Currency",
  payPer: "Per",
  // Opportunities O8: the freelance and project feeds of an old sweep's summary.
  freelanceSources: "Freelance and project sources",
  addMissionByHand: "Add a mission by hand",
  payInvalid:
    "The pay needs whole numbers from 1 to 10,000,000, the lowest first, a currency and a period. Leave both figures empty for none.",
  // The four plain lists of the search (owner, 2026-10-27); "places" above is the third.
  jobTitles: "Job titles",
  unwantedTitles: "Unwanted titles",
  unwantedPlaces: "Unwanted places",
  // A search list saved as regular expressions before the setup asked for words only
  // (owner, 2026-10-28): its terms keep working as they are.
  listUsesRegularExpressions:
    "This list was saved as patterns by an earlier version. They keep working as they are, and the search reads the words in each.",
} as const;

/** The empty deck line when the profile has no skill: every fit is near 0. */
export function deckNoSkills(threshold: number): string {
  return `Your profile has no skills yet, so new postings score close to 0 and stay under your threshold of ${threshold}.`;
}
export const DECK_NO_SKILLS_HINT =
  "Add the skills you have in your profile, or let Fill my profile read your CV. Then choose Rescore all postings in the menu of the Jobs page.";

/** The empty deck line; it names the fit threshold in force so the number is never a mystery. */
export function deckEmpty(threshold: number): string {
  return `Nothing new above your threshold of ${threshold}.`;
}
