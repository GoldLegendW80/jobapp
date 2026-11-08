import { WIZARD_STEP_WORDS } from "@/lib/wording";
import {
  CURRENCIES,
  PAY_MAX,
  type Answers,
  type COMPANY_TYPES,
  type DOCUMENTS_LANGUAGES,
  type DOCUMENTS_TONES,
  type PayRange,
  type Preferences,
  type REMOTE_STANCES,
  type SalaryRange,
  type Search,
  type SectionData,
  type SENIORITIES,
  type TermList,
} from "@/features/profile/api/profile-api";
import { SALARY_RANGES_MAX } from "@/features/profile/helpers/forms";

/**
 * The ten steps of /welcome (SPEC-opportunities, "The first login wizard"), the answers the
 * person types (the draft, kept while they go back and forth), and how the draft reads from and
 * writes to the profile. Pure: the wizard's hook (use-wizard.ts) does the saving.
 */
export const STEP_IDS = [
  "access",
  "profile",
  "building",
  "looking",
  "where",
  "sites",
  "work",
  "pay",
  "apply",
  "summary",
] as const;
export type StepId = (typeof STEP_IDS)[number];

export const STEP_NAMES: Record<StepId, string> = WIZARD_STEP_WORDS;

/**
 * Steps the onboarding api does not keep among the steps passed: "Model access" (2026-11-07) is
 * answered by the credential saved, "Building your profile" (2026-11-07) by the profile it
 * fills, and "Websites to search" (2026-11-04) by the list itself, so none is sent to the api,
 * and a release before them never reads a step it does not know. Each counts as passed once a
 * later step is.
 */
export const UNSTORED: ReadonlySet<StepId> = new Set([
  "access",
  "building",
  "sites",
]);

/** Steps that cannot be skipped: the roles, and the summary that ends the wizard. */
export const ESSENTIAL: ReadonlySet<StepId> = new Set(["looking", "summary"]);

export const isStepId = (value: string | null): value is StepId =>
  value !== null && (STEP_IDS as readonly string[]).includes(value);

export interface PayDraft {
  min: string;
  max: string;
  currency: string;
}

export type YesNo = "yes" | "no";

/** The first country of `answers.work_authorization`, the one the wizard asks about. */
export interface AuthorizationDraft {
  country: string;
  /** "May you work there?"; null while not chosen */
  allowed: YesNo | null;
  sponsorship: YesNo | null;
  /** The stored answer of that country, kept while `allowed` stays what it says (unchosen when
   * it is more than yes or no). */
  kept: string;
}

export interface Draft {
  roles: string;
  seniority: (typeof SENIORITIES)[number][];
  industries: string;
  companyTypes: (typeof COMPANY_TYPES)[number][];
  locations: string;
  anywhere: boolean;
  remote: (typeof REMOTE_STANCES)[number] | null;
  contracts: string[];
  salary: PayDraft;
  daily: PayDraft;
  ratherNotSay: boolean;
  /** answers.motivation, answers.notice_period and answers.earliest_start as typed */
  motivation: string;
  notice: string;
  start: string;
  authorization: AuthorizationDraft;
  language: (typeof DOCUMENTS_LANGUAGES)[number];
  tone: (typeof DOCUMENTS_TONES)[number];
  /** The mode after a Like the person picked here; null keeps apply's setting untouched. */
  mode: "review" | null;
  /**
   * "Let me review each filled form before it is sent" (apply_settings.review_before_send) as
   * ticked here; null keeps apply's setting untouched.
   */
  review: boolean | null;
}

/** What every step's panel gets: the draft, a change to it, and what the page knows. */
export interface PanelProps {
  draft: Draft;
  change: (patch: Partial<Draft>) => void;
  /** The saved lists are patterns, which the plain fields cannot show. */
  patterns?: { roles: boolean; places: boolean };
  problem?: string | null;
}

/** The list with `item` once (at the end) when `on`, without it otherwise. */
export function toggled<T>(list: readonly T[], item: T, on: boolean): T[] {
  return on
    ? [...list.filter((x) => x !== item), item]
    : list.filter((x) => x !== item);
}

const join = (list: TermList): string => list.terms.join(", ");

/** "a, b ,, a" -> ["a", "b"]: trimmed, empty ones dropped, each once (case kept). */
export function splitTerms(text: string): string[] {
  const seen = new Set<string>();
  const found: string[] = [];
  for (const raw of text.split(/[,\n]/)) {
    const term = raw.trim().replace(/\s+/g, " ");
    const key = term.toLocaleLowerCase();
    if (term && !seen.has(key)) {
      seen.add(key);
      found.push(term);
    }
  }
  return found;
}

const payDraft = (range: PayRange | null, fallback: string): PayDraft =>
  range
    ? {
        min: String(range.min),
        max: String(range.max),
        currency: range.currency,
      }
    : { min: "", max: "", currency: fallback };

/** "Yes" or "No" when an answer starts with that word, else null. */
export function yesNoOf(answer: string): YesNo | null {
  const word = /^\s*(yes|no)\b/i.exec(answer)?.[1]?.toLowerCase();
  return word === "yes" || word === "no" ? word : null;
}

const yesNo = (value: boolean | null): YesNo | null =>
  value === null ? null : value ? "yes" : "no";

function authorizationDraft(answers: Answers): AuthorizationDraft {
  const first = answers.work_authorization[0];
  if (!first)
    return { country: "", allowed: null, sponsorship: null, kept: "" };
  return {
    country: first.country,
    allowed: yesNoOf(first.answer),
    sponsorship: yesNo(first.needs_sponsorship),
    kept: first.answer,
  };
}

/** "Rather not say" as the Pay step shows it: the stance chosen for forms, and no pay saved. */
const notSaid = (sections: SectionData): boolean =>
  sections.answers.salary_stance === "leave_blank" &&
  sections.search.compensation.salary === null &&
  sections.search.compensation.daily_rate === null;

export function draftOf(sections: SectionData): Draft {
  const search = sections.search;
  const prefs = sections.preferences;
  const answers = sections.answers;
  return {
    roles: join(search.title_include),
    seniority: [...search.seniority],
    industries: join(search.industries),
    companyTypes: [...search.company_types],
    locations: join(search.location),
    anywhere:
      search.location.terms.length === 0 && search.remote_stance !== null,
    remote: search.remote_stance,
    contracts: [...search.contract_types],
    salary: payDraft(search.compensation.salary, "EUR"),
    daily: payDraft(search.compensation.daily_rate, "EUR"),
    ratherNotSay: notSaid(sections),
    motivation: answers.motivation ?? "",
    notice: answers.notice_period ?? "",
    start: answers.earliest_start ?? "",
    authorization: authorizationDraft(answers),
    language: prefs.documents_language,
    tone: prefs.documents_tone,
    mode: null,
    review: null,
  };
}

/** A term list as the person typed it; one in advanced (pattern) mode is never rewritten here. */
function terms(stored: TermList, text: string): TermList {
  if (stored.advanced_regex) return stored;
  return { ...stored, terms: splitTerms(text) };
}

export type PayCheck = { range: PayRange | null } | { error: string };

/** Both figures or neither; whole numbers from 0 to PAY_MAX; min at most max; a known currency. */
export function readPay(draft: PayDraft): PayCheck {
  const min = draft.min.trim();
  const max = draft.max.trim();
  if (!min && !max) return { range: null };
  if (!min || !max) return { error: "Give both figures, or leave both empty." };
  if (!/^[0-9]{1,8}$/.test(min) || !/^[0-9]{1,8}$/.test(max))
    return { error: "Use whole numbers, without spaces or symbols." };
  const low = Number(min);
  const high = Number(max);
  if (high > PAY_MAX)
    return { error: `At most ${PAY_MAX.toLocaleString("en")}.` };
  if (low > high) return { error: "The first figure is the lower one." };
  if (!(CURRENCIES as readonly string[]).includes(draft.currency))
    return { error: "Choose a currency from the list." };
  return { range: { min: low, max: high, currency: draft.currency } };
}

/** What stops Next on a step, in a sentence, or null. */
export function stepProblem(step: StepId, draft: Draft): string | null {
  if (step === "looking" && splitTerms(draft.roles).length === 0)
    return "Name at least one role, for example Data engineer.";
  if (step === "where") {
    if (!draft.anywhere && splitTerms(draft.locations).length === 0)
      return 'Name at least one place, or choose "Anywhere".';
    if (draft.remote === null) return "Choose remote, hybrid, on site or any.";
  }
  if (step === "work") {
    const { country, allowed, sponsorship, kept } = draft.authorization;
    if (!country.trim() && (allowed !== null || sponsorship !== null))
      return "Name the country or region where you may work, for example France.";
    if (country.trim() && allowed === null && !kept)
      return `Say whether you may work in ${country.trim()}.`;
  }
  if (step === "pay" && !draft.ratherNotSay) {
    for (const [name, pay] of [
      ["salary", draft.salary],
      ["daily rate", draft.daily],
    ] as const) {
      const read = readPay(pay);
      if ("error" in read) return `Your ${name}: ${read.error}`;
    }
  }
  return null;
}

/** The search section with this step's answers in it (steps looking to pay), from `stored`. */
export function searchWith(step: StepId, stored: Search, draft: Draft): Search {
  switch (step) {
    case "looking":
      return {
        ...stored,
        title_include: terms(stored.title_include, draft.roles),
        seniority: draft.seniority,
        industries: terms(stored.industries, draft.industries),
        company_types: draft.companyTypes,
      };
    case "where":
      return {
        ...stored,
        location: terms(stored.location, draft.anywhere ? "" : draft.locations),
        remote_stance: draft.remote,
      };
    case "work":
      return { ...stored, contract_types: draft.contracts };
    case "pay":
      return { ...stored, compensation: compensationOf(draft) };
    default:
      return stored;
  }
}

type Compensation = Search["compensation"];

function compensationOf(draft: Draft): Compensation {
  if (draft.ratherNotSay) return { salary: null, daily_rate: null };
  const salary = readPay(draft.salary);
  const daily = readPay(draft.daily);
  return {
    salary: "range" in salary ? salary.range : null,
    daily_rate: "range" in daily ? daily.range : null,
  };
}

/**
 * The ranges forms get for a pay: the yearly salary, then the daily rate. Apply reads one range
 * per currency, so a daily rate in the salary's currency stays on the cards only.
 */
export function rangesOf(pay: Compensation): SalaryRange[] {
  const ranges: SalaryRange[] = [];
  for (const [range, period] of [
    [pay.salary, "year"],
    [pay.daily_rate, "day"],
  ] as const) {
    if (range && !ranges.some((one) => one.currency === range.currency))
      ranges.push({
        currency: range.currency,
        minimum: range.min,
        maximum: range.max,
        period,
      });
  }
  return ranges;
}

const sameRange = (a: SalaryRange, b: SalaryRange) =>
  a.currency === b.currency &&
  a.minimum === b.minimum &&
  a.maximum === b.maximum &&
  a.period === b.period;

/**
 * The salary forms get, from the Pay step: "Rather not say" leaves it blank; a pay given is its
 * ranges, replacing the ones this step wrote from the pay saved before (`before`) and those of
 * the same currency, every other range kept. Unticking "Rather not say" with no figure clears
 * the stance it showed.
 */
function salaryWith(stored: Answers, draft: Draft, before: Compensation) {
  if (draft.ratherNotSay)
    return { ...stored, salary_stance: "leave_blank" as const };
  const fresh = rangesOf(compensationOf(draft));
  const old = rangesOf(before);
  const kept = stored.salary_ranges.filter(
    (range) =>
      !old.some((one) => sameRange(one, range)) &&
      !fresh.some((one) => one.currency === range.currency),
  );
  const ranges = [...fresh, ...kept].slice(0, SALARY_RANGES_MAX);
  const shown =
    stored.salary_stance === "leave_blank" &&
    before.salary === null &&
    before.daily_rate === null;
  const stance = fresh.length
    ? "range"
    : shown || (stored.salary_stance === "range" && ranges.length === 0)
      ? null
      : stored.salary_stance;
  return { ...stored, salary_stance: stance, salary_ranges: ranges };
}

/**
 * The first country of the work authorisation as typed, the others kept (each country once).
 * Nothing typed leaves the list as stored; stepProblem refuses a choice without its country.
 */
function authorizationsWith(
  stored: Answers["work_authorization"],
  draft: AuthorizationDraft,
): Answers["work_authorization"] {
  const country = draft.country.trim();
  if (!country) return stored;
  const answer =
    draft.allowed === null
      ? draft.kept
      : yesNoOf(draft.kept) === draft.allowed
        ? draft.kept
        : draft.allowed === "yes"
          ? "Yes"
          : "No";
  const key = country.toLocaleLowerCase();
  return [
    {
      country,
      answer,
      needs_sponsorship:
        draft.sponsorship === null ? null : draft.sponsorship === "yes",
    },
    ...stored
      .slice(1)
      .filter((one) => one.country.trim().toLocaleLowerCase() !== key),
  ];
}

const textOrNull = (text: string): string | null => text.trim() || null;

/**
 * The answers section with this step's answers in it, from `stored`; every other answer kept.
 * Looking writes the motivation, Work the notice, the start and the work authorisation, Pay the
 * salary forms get (`before`: the pay saved before this step).
 */
export function answersWith(
  step: StepId,
  stored: Answers,
  draft: Draft,
  before: Compensation,
): Answers {
  switch (step) {
    case "looking":
      return { ...stored, motivation: textOrNull(draft.motivation) };
    case "work":
      return {
        ...stored,
        notice_period: textOrNull(draft.notice),
        earliest_start: textOrNull(draft.start),
        work_authorization: authorizationsWith(
          stored.work_authorization,
          draft.authorization,
        ),
      };
    case "pay":
      return salaryWith(stored, draft, before);
    default:
      return stored;
  }
}

export function preferencesWith(
  stored: Preferences,
  draft: Draft,
): Preferences {
  return {
    ...stored,
    documents_language: draft.language,
    documents_tone: draft.tone,
  };
}

// --- progress -----------------------------------------------------------------------------------

/**
 * The steps passed (Next or Skip) come from the profile (`passed` of the onboarding state, kept
 * by POST /api/opportunities/onboarding/steps): the profile cannot tell a skipped step from one
 * never seen, so this is what lets a closed tab, or another device, resume at the right step.
 */
export function passedOf(values: readonly string[]): StepId[] {
  return STEP_IDS.filter(
    (step) =>
      values.includes(step) ||
      (UNSTORED.has(step) &&
        STEP_IDS.slice(STEP_IDS.indexOf(step) + 1).some((later) =>
          values.includes(later),
        )),
  );
}

/** The first step neither answered by the profile (`done`) nor passed; the summary last. */
export function resumeStep(
  done: readonly string[],
  passed: readonly StepId[],
): StepId {
  return (
    STEP_IDS.find((step) => !done.includes(step) && !passed.includes(step)) ??
    "summary"
  );
}

/**
 * The step to open for `?step=`: the one asked for, unless no role is saved yet and it lies
 * after the roles (a step typed in the address never reaches Start without a role): then the
 * first step not done, the roles at the latest.
 */
export function openStep(
  requested: string | null,
  done: readonly string[],
  passed: readonly StepId[],
): StepId {
  // The roles count as passed only while saved: the first step left is the roles at the latest.
  const first = resumeStep(
    done,
    passed.filter((step) => step !== "looking"),
  );
  if (!isStepId(requested)) return first;
  if (
    !done.includes("looking") &&
    STEP_IDS.indexOf(requested) > STEP_IDS.indexOf("looking")
  )
    return first;
  return requested;
}

/** Where "Start" leads: the deck. */
export const DISCOVER = "/discover";
/** Where "Start" leads when the list of websites is empty: Find jobs, open. */
export const FIND_JOBS = "/jobs?find=1";

/** Any origin will do: `nextPage` only checks that the address stays on it. */
const SAME_ORIGIN = "https://app.invalid";
/** A control character, any white space, or a backslash: never in a path this app links to. */
const UNSAFE = /[\p{Cc}\s\\]/u;

/**
 * The page asked for when the wizard opened by itself (`?next=`, set by the gate): restored when
 * the wizard ends or is put off. Only a path of this app, never /welcome itself: it starts with
 * one slash, holds no control character, white space or backslash (as typed, nor in its path
 * once percent-decoded, so a tab smuggled in as `/%09/evil.com` is refused), and resolves to the
 * page's own origin (`new URL`). Anything else is refused.
 */
export function nextPage(value: string | null): string | null {
  if (!value?.startsWith("/") || value.startsWith("//")) return null;
  if (UNSAFE.test(value)) return null;
  let url: URL;
  let path: string;
  try {
    url = new URL(value, SAME_ORIGIN);
    path = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (url.origin !== SAME_ORIGIN) return null;
  if (UNSAFE.test(path) || path.startsWith("//")) return null;
  if (path.toLowerCase().startsWith("/welcome")) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}
