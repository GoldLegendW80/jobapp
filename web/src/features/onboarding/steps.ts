import { WIZARD_STEP_WORDS } from "@/lib/wording";
import {
  CURRENCIES,
  PAY_MAX,
  type COMPANY_TYPES,
  type DOCUMENTS_LANGUAGES,
  type DOCUMENTS_TONES,
  type PayRange,
  type Preferences,
  type REMOTE_STANCES,
  type Search,
  type SectionData,
  type SENIORITIES,
  type TermList,
} from "@/features/profile/api";

/**
 * The seven steps of /welcome (SPEC-opportunities, "The first login wizard"), the answers the
 * person types (the draft, kept while they go back and forth), and how the draft reads from and
 * writes to the profile. Pure: the page (welcome-page.tsx) does the saving.
 */
export const STEP_IDS = [
  "profile",
  "looking",
  "where",
  "work",
  "pay",
  "apply",
  "summary",
] as const;
export type StepId = (typeof STEP_IDS)[number];

export const STEP_NAMES: Record<StepId, string> = WIZARD_STEP_WORDS;

/** Steps that cannot be skipped: the roles, and the summary that ends the wizard. */
export const ESSENTIAL: ReadonlySet<StepId> = new Set(["looking", "summary"]);

export const isStepId = (value: string | null): value is StepId =>
  value !== null && (STEP_IDS as readonly string[]).includes(value);

export interface PayDraft {
  min: string;
  max: string;
  currency: string;
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
  language: (typeof DOCUMENTS_LANGUAGES)[number];
  tone: (typeof DOCUMENTS_TONES)[number];
  /** The mode after a Like the person picked here; null keeps apply's setting untouched. */
  mode: "review" | null;
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

export function draftOf(sections: SectionData): Draft {
  const search = sections.search;
  const prefs = sections.preferences;
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
    ratherNotSay: false,
    language: prefs.documents_language,
    tone: prefs.documents_tone,
    mode: null,
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
    case "pay": {
      if (draft.ratherNotSay)
        return { ...stored, compensation: { salary: null, daily_rate: null } };
      const salary = readPay(draft.salary);
      const daily = readPay(draft.daily);
      return {
        ...stored,
        compensation: {
          salary: "range" in salary ? salary.range : null,
          daily_rate: "range" in daily ? daily.range : null,
        },
      };
    }
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
  return STEP_IDS.filter((step) => values.includes(step));
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

/**
 * What the summary shows: the answers as saved (the stored profile and apply's settings), never
 * what was typed and then skipped. Only "Rather not say" comes from what was typed, since it
 * saves as no figures at all.
 */
export function savedDraft(sections: SectionData, typed: Draft): Draft {
  const stored = draftOf(sections);
  const pay = sections.search.compensation;
  const nothingStored = pay.salary === null && pay.daily_rate === null;
  return { ...stored, ratherNotSay: typed.ratherNotSay && nothingStored };
}

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
  if (path.startsWith("/welcome")) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}
