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

/** A code as words when no word is known for it: "cv_writer" reads "Cv writer". */
export function readableCode(code: string): string {
  const words = code.replace(/[_\s]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : code;
}

/**
 * What each assistant task is called on Assistant activity (/runs), by the agent id of
 * api/app/agents: the agents of agents/definitions.py and every `definitions.register` of the
 * other modules (browser, apply, ingestion, documents). An id this build does not know reads
 * as words (readableCode), never as the raw id.
 */
export const AGENT_WORDS: Record<string, string> = {
  echo: "Test agent",
  triage: "Posting check",
  job_chat: "Chat about a posting",
  browser_finder: "Website reading",
  browser_discovery: "Website search",
  apply_form: "Filling an application form",
  apply_answer: "Answer to an application question",
  apply_check: "Check before applying",
  profile_extract: "Reading your documents",
  document_transcribe: "Reading a scanned page",
  documents_cv: "CV for a posting",
  documents_letter: "Cover letter",
  documents_base: "Base CV",
  documents_design: "Design taken from your CV",
  documents_proposal: "Proposal message",
  documents_chat_cv: "Chat about a CV",
  documents_chat_letter: "Chat about a cover letter",
  documents_chat_base: "Chat about a base CV",
  profile_titles: "Job title ideas",
};

export function agentWord(id: string): string {
  return AGENT_WORDS[id] ?? readableCode(id);
}

/** The raw end codes a run can carry (Claude Code's result subtypes), as sentences. */
const RUN_ERROR_WORDS: Record<string, string> = {
  max_turns: "The run reached its limit of turns.",
  error_max_turns: "The run reached its limit of turns.",
  error_during_execution: "The assistant stopped on an error while it worked.",
  timeout: "The run took longer than its time limit and was stopped.",
  budget: "The run reached its token budget and was stopped.",
  cancelled: "The run was cancelled.",
};

/**
 * A run's stored error as a sentence. The runner stores sentences (api/app/agents/failures.py),
 * which are kept; a bare code ("max_turns"), or one inside "The agent failed (...)", becomes
 * words.
 */
export function runErrorWords(error: string): string {
  const text = error.trim();
  const code = /^[a-z][a-z0-9_]*$/.test(text)
    ? text
    : /^The agent failed \(([a-z][a-z0-9_]*)\)\.$/.exec(text)?.[1];
  if (code === undefined) return text;
  return (
    RUN_ERROR_WORDS[code] ??
    `The run stopped: ${readableCode(code).toLowerCase()}.`
  );
}

/**
 * Completing the person's profile on a site (apply, SPEC-apply "Complete my profile on a
 * site"): the states of the run (api/app/apply/site_profile.py, SiteRunState) as sentences.
 */
export const SITE_PROFILE_STATE_WORDS: Record<string, string> = {
  queued: "Waiting for your browser to be free.",
  filling: "The assistant is filling in your profile on this site.",
  needs_person: "Waiting for you.",
  ready: "Ready for your review.",
  saving: "Saving your changes on the site.",
  done: "Done: your profile on this site is complete.",
  cancelled: "Stopped. Nothing more is done on this site.",
  failed: "Stopped before the end.",
};

export function siteProfileStateWord(state: string): string {
  return SITE_PROFILE_STATE_WORDS[state] ?? `${readableCode(state)}.`;
}

/** Why completing the profile on a site, or one of its sections, needs the person, by code. */
export const SITE_PROFILE_PROBLEM_WORDS: Record<string, string> = {
  linkedin_off:
    "LinkedIn is off in your Account, so the assistant did not open it.",
  fill_interrupted:
    "The filling stopped before it ended. Start again when you are ready.",
  save_unknown:
    "The app cannot tell whether the site saved your changes. Check them on the site.",
  budget_exhausted:
    "The assistant reached its limit before it finished this section.",
  approval_expired: "Your approval expired. Look at this section again.",
  screenshot_or_save_missing:
    "The app could not capture this page or find where the site saves it. Check it on the site.",
  section_missing: "This section is no longer on the site.",
};

export function siteProfileProblem(code: string): string {
  return (
    SITE_PROFILE_PROBLEM_WORDS[code] ??
    `This needs your review: ${readableCode(code).toLowerCase()}.`
  );
}

/** A template's tags (documents D7, the gallery), as words; a tag not listed shows as it is. */
export const TEMPLATE_TAG_WORDS: Record<string, string> = {
  "ats friendly": "Easy for hiring software to read",
};

export function templateTagWord(tag: string): string {
  return TEMPLATE_TAG_WORDS[tag] ?? tag;
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
  access: "Model access",
  profile: "Your profile",
  building: "Building your profile",
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
  contractsChooseAny: "Contracts (choose any)",
  ratherNotSay: "Rather not say",
  yearlySalary: "Yearly salary",
  dailyRateFreelance: "Daily rate (freelance)",
  whyLooking: "Why you are looking",
  noticePeriod: "Notice period",
  earliestStart: "Earliest start",
  workAuthorisation: "Work authorisation",
  countryOrRegion: "Country or region",
  mayWorkThere: "May you work there?",
  needsSponsorship: "Do you need a visa sponsorship?",
  afterLike: "After a Like",
  whenYouClickApply: "When you click Apply on a posting",
  reviewEachForm: "Let me review each filled form before it is sent",
  documentsLanguage: "Language of your CV and letters",
  tone: "Tone",
  change: "Change",
  readProfile: "Read them into your profile",
  reviewProposals: "Review the proposals",
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
  analyzeYourComputer: "Analyze your computer",
  applicationSite: "Application site",
  findNewPostings: "Find new postings",
  rolesToNeverApplyFor: "Roles to never apply for",
  fitScore: "Fit score",
  // The two thresholds, each card names its own so nobody confuses them (documents are
  // written on a click only since D29: no threshold of their own).
  showPostingsFrom: "Show postings from",
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
  yourDeck: "Your deck",
  changeTheMode: "Change the mode",
  matches: "Matches",
  gap: "Gap",
  // The summary's optional model key (welcome step 8), saved as Account's Model access does.
  yourApiKeyOptional: "Your Anthropic API key (optional)",
  apiKey: "API key",
  showApiKey: "Show API key",
  saveKey: "Save key",
  verify: "Verify",
  anthropicConsole: "Anthropic Console",
  // The upload of a CV (documents/upload-panel.tsx): true with or without a model key.
  uploadCvHint:
    "Drop a PDF or a Word document here, or choose it. The app keeps the file in your documents and reads its text. Turning it into a CV you can edit uses your Anthropic API key: until one is saved, that part waits.",
  applicationSettings: "Application settings",
  // The posting page: Apply and the documents first, the rest folded.
  yourApplication: "Your application",
  moreAboutPosting: "More about this posting",
  // The Jobs list's detail pane: the selected posting on its own page.
  openFullPage: "Open full page",
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
  // Audit chapter 2: plain words for the screens that showed codes or jargon.
  assistantActivity: "Assistant activity",
  allActivity: "All activity",
  // The live view (SPEC-browser): the person takes the keyboard and the mouse, the assistant's
  // next action waits; giving them back lets it go on.
  useBrowserMyself: "Use the browser myself",
  letAssistantContinue: "Let the assistant continue",
  easyForHiringSoftware: "Easy for hiring software to read",
  // Profile, Skills: a gate is a requirement the person does not meet (api/app/jobs/scoring.py).
  requirementsNotMet: "Requirements you do not meet",
  addRequirement: "Add a requirement",
  noneYet: "None yet.",
  pointsTakenOff: "Points taken off",
  // Ingestion: a run reads the documents.
  startReading: "Start reading",
  startAnotherReading: "Start another reading",
  // The button that starts a run, before its token range.
  completeProfile: "Complete my profile",
  readingPausedAtLimit: "The reading paused at the limit you agreed to.",
  serverUnreachable: "Server unreachable",
  checkServerAgain: "Check the server again",
  sessionEnded: "Your session ended. Sign in again to go back where you were.",
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

/** The job title ideas under Job titles and the place idea under Places (profile/title-suggestions.tsx). */
export const SUGGESTION_WORDS = {
  heading: "Suggested for you",
  needsKey: "Job title ideas from your profile need a model key.",
  addKey: "Add a model key",
  needsInput:
    "Add your CV, fill your profile or tell the assistant what you want to do, and job title ideas show here.",
  addCv: "Add your CV",
  /** In a dialog, where a link would drop the draft: the page, named. */
  needsKeyThere:
    "Job title ideas from your profile need a model key, added on the Account page.",
  needsInputThere:
    "Job title ideas show here once you add your CV, fill your profile or tell the assistant what you want to do.",
  first: "Get job title ideas that fit your profile.",
  suggest: "Suggest titles",
  more: "More ideas",
  retry: "Try again",
  running: "Finding titles that fit your profile",
  none: "No new title to suggest.",
  notForMe: "Not for me",
  chatLabel: "Tell the assistant what you want to do",
  chatPlaceholder:
    "For example: move into product, stay in data, work remotely",
  send: "Send",
  lastMessage: (text: string) => `You told the assistant: “${text}”`,
  earlier: (count: number) =>
    `It also reads your ${count} earlier ${count === 1 ? "message" : "messages"}.`,
  clear: "Clear",
  clearName: "Clear what you told the assistant",
  fromProfile: "from your profile",
} as const;
