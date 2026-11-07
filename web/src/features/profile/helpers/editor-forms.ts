/**
 * Form values, zod schemas and conversions of the skills and search editors. Like forms.ts, the
 * schemas mirror api/app/profile/schemas.py so most mistakes are caught before a save: term rules
 * (plain terms at most 200 characters; advanced ones compiled, at most 500 characters, no nested
 * quantifiers; see matching.ts), category names unique across have, adjacent and none, gate
 * penalties 0 to 100. The api stays the judge: its 422 lands on the field, or the term, it names
 * (errors.ts).
 */
import { z } from "zod";

import {
  COMPANY_TYPES,
  CONTRACT_TYPES,
  CURRENCIES,
  INDUSTRIES_MAX,
  PAY_MAX,
  REMOTE_STANCES,
  SENIORITIES,
  BROWSER_KINDS,
  type PayRange,
  type BrowserSource,
  type Category,
  type Gate,
  type Search,
  type Skills,
  type TermList,
  WEEKDAYS,
} from "../api/profile-api";
import { fold, LINE_MAX, text, uniqueBy } from "./forms";
import { cleanTerms, listProblems, termProblem } from "./matching";

export const TERMS_MAX = 300;
export const CATEGORIES_MAX = 100;
export const GATES_MAX = 50;
export const NEVER_APPLY_MAX = 50;
export const NEVER_APPLY_LINE = 500;
/** schemas.py SearchSection.browser_sources: at most 10 sources. */
export const BROWSER_SOURCES_MAX = 10;

const cleanLine = (value: string) => value.normalize("NFC").trim();

// --- term lists ------------------------------------------------------------------------------

const termListShape = {
  advanced_regex: z.boolean(),
  terms: z.array(z.string()).max(TERMS_MAX, `Keep at most ${TERMS_MAX} terms.`),
};

/**
 * An issue on each term the api would refuse, at `<list>.terms.<index>`: alone, or with the
 * terms before it (a group name used twice).
 */
function checkTerms(list: TermList, ctx: z.RefinementCtx) {
  const across = listProblems(list.terms, list.advanced_regex);
  list.terms.forEach((term, index) => {
    const problem = termProblem(term, list.advanced_regex) ?? across.get(index);
    if (problem) {
      ctx.addIssue({
        code: "custom",
        message: problem,
        path: ["terms", index],
      });
    }
  });
}

export const termListSchema = z.object(termListShape).superRefine(checkTerms);
export type TermListForm = z.infer<typeof termListSchema>;

const toTermList = (list: TermList): TermListForm => ({
  advanced_regex: list.advanced_regex,
  terms: [...list.terms],
});
const fromTermList = (list: TermListForm): TermList => ({
  advanced_regex: list.advanced_regex,
  terms: cleanTerms(list.terms),
});

// --- skills ----------------------------------------------------------------------------------

const categorySchema = z
  .object({
    name: text("a name", LINE_MAX, { required: true }),
    ...termListShape,
  })
  .superRefine(checkTerms);
const gateSchema = z
  .object({
    name: text("a name", LINE_MAX, { required: true }),
    ...termListShape,
    penalty: z.number().int().min(0).max(100),
  })
  .superRefine(checkTerms);

export const SKILL_LISTS = ["have", "adjacent", "none"] as const;
export type SkillList = (typeof SKILL_LISTS)[number];

const YEARS = /^\d{1,2}(?:\.\d{1,2})?$/;

export const skillsSchema = z
  .object({
    years_experience: z
      .string()
      .refine(
        (v) => v.trim() === "" || (YEARS.test(v.trim()) && Number(v) <= 60),
        "Enter a number of years between 0 and 60, such as 3.5.",
      ),
    have: z.array(categorySchema).max(CATEGORIES_MAX),
    adjacent: z.array(categorySchema).max(CATEGORIES_MAX),
    none: z.array(categorySchema).max(CATEGORIES_MAX),
    gates: z
      .array(gateSchema)
      .max(GATES_MAX, `Keep at most ${GATES_MAX} requirements.`),
  })
  .superRefine((values, ctx) => {
    // Category names are unique across the three lists, as the api requires.
    const seen = new Set<string>();
    for (const list of SKILL_LISTS) {
      values[list].forEach((category, index) => {
        const key = fold(category.name);
        if (key === "") return;
        if (seen.has(key)) {
          ctx.addIssue({
            code: "custom",
            message: "Another category already has this name.",
            path: [list, index, "name"],
          });
        }
        seen.add(key);
      });
    }
    uniqueBy(
      values.gates,
      (g) => fold(g.name),
      "name",
      "Another requirement already has this name.",
      ctx,
      "gates",
    );
  });
export type SkillsForm = z.infer<typeof skillsSchema>;
export type CategoryForm = SkillsForm["have"][number];
export type GateForm = SkillsForm["gates"][number];

export const NEW_CATEGORY: CategoryForm = {
  name: "",
  advanced_regex: false,
  terms: [],
};
export const NEW_GATE: GateForm = {
  name: "",
  advanced_regex: false,
  terms: [],
  penalty: 25,
};

const toCategory = (c: Category): CategoryForm => ({
  name: c.name,
  ...toTermList(c),
});
const fromCategory = (c: CategoryForm): Category => ({
  name: cleanLine(c.name),
  ...fromTermList(c),
});

export const toSkillsForm = (skills: Skills): SkillsForm => ({
  years_experience:
    skills.years_experience === null ? "" : String(skills.years_experience),
  have: skills.have.map(toCategory),
  adjacent: skills.adjacent.map(toCategory),
  none: skills.none.map(toCategory),
  gates: skills.gates.map((g) => ({ ...toCategory(g), penalty: g.penalty })),
});

export const fromSkillsForm = (values: SkillsForm): Skills => ({
  years_experience:
    values.years_experience.trim() === ""
      ? null
      : Number(values.years_experience),
  have: values.have.map(fromCategory),
  adjacent: values.adjacent.map(fromCategory),
  none: values.none.map(fromCategory),
  gates: values.gates.map((g): Gate => ({
    ...fromCategory(g),
    penalty: g.penalty,
  })),
});

// --- advanced matching: the regular expressions About you does not hold ------------------------

const regex = <T extends { advanced_regex: boolean }>(items: readonly T[]) =>
  items.filter((item) => item.advanced_regex);
const words = <T extends { advanced_regex: boolean }>(items: readonly T[]) =>
  items.filter((item) => !item.advanced_regex);

/** The regular expression categories and requirements of `skills`, without the years. */
const advancedOf = (skills: Skills): Skills => ({
  years_experience: null,
  have: regex(skills.have),
  adjacent: regex(skills.adjacent),
  none: regex(skills.none),
  gates: regex(skills.gates),
});

/** The form of Advanced matching: the regular expression categories and requirements only. */
export const toAdvancedForm = (skills: Skills): SkillsForm =>
  toSkillsForm(advancedOf(skills));

/**
 * The skills to save: `advanced` (what Advanced matching holds) first in each list, then the
 * categories in words and the years of `current`, the skills as stored now. An item whose switch
 * was turned off goes with them, written in words from then on.
 */
export const withAdvanced = (current: Skills, advanced: Skills): Skills => ({
  years_experience: current.years_experience,
  have: [...advanced.have, ...words(current.have)],
  adjacent: [...advanced.adjacent, ...words(current.adjacent)],
  none: [...advanced.none, ...words(current.none)],
  gates: [...advanced.gates, ...words(current.gates)],
});

/** The categories and requirements written in words, as About you holds them. */
export const inWords = (skills: Skills): Skills =>
  withAdvanced(skills, {
    years_experience: null,
    have: [],
    adjacent: [],
    none: [],
    gates: [],
  });

/** The form of the Skills fields of About you without a model key: the years and the words. */
export const toWordsForm = (skills: Skills): SkillsForm =>
  toSkillsForm(inWords(skills));

/**
 * The skills to save from those fields: the regular expressions of `current` (the skills as
 * stored now) first in each list, as stored, then `words` with its years.
 */
export const withWords = (current: Skills, words: Skills): Skills =>
  withAdvanced(words, advancedOf(current));

// --- search ----------------------------------------------------------------------------------

const browserSourceSchema = z.object({
  kind: z.enum(BROWSER_KINDS),
  query: text("the search words", LINE_MAX, { required: true }),
  location: text("a location", LINE_MAX),
  enabled: z.boolean(),
});

const YEAR_DAYS = /^\d{1,3}$/;
/** schemas.py DocumentsGeneration: the safety ceiling, postings a day. */
const CEILING = /^\d{1,3}$/;
export const DAILY_CEILING_MAX = 500;
export const DAILY_CEILING_PROBLEM = `The safety ceiling is a whole number of postings a day, from 1 to ${DAILY_CEILING_MAX}.`;

// --- the pay sought (O4) ----------------------------------------------------------------------

const WHOLE = /^\d{1,8}$/;
const PAY_PROBLEM = `Enter a whole number from 0 to ${PAY_MAX.toLocaleString("en")}.`;

/** A pay as typed: both figures empty means none; else whole numbers, min at most max. */
const payFormSchema = z
  .object({ min: z.string(), max: z.string(), currency: z.string() })
  .superRefine((pay, ctx) => {
    const min = pay.min.trim();
    const max = pay.max.trim();
    if (min === "" && max === "") return;
    const ok = (v: string) => WHOLE.test(v) && Number(v) <= PAY_MAX;
    if (!ok(min))
      ctx.addIssue({ code: "custom", message: PAY_PROBLEM, path: ["min"] });
    if (!ok(max))
      ctx.addIssue({ code: "custom", message: PAY_PROBLEM, path: ["max"] });
    if (ok(min) && ok(max) && Number(min) > Number(max))
      ctx.addIssue({
        code: "custom",
        message: "The maximum is below the minimum.",
        path: ["max"],
      });
    if (!(CURRENCIES as readonly string[]).includes(pay.currency))
      ctx.addIssue({
        code: "custom",
        message: "Choose a currency.",
        path: ["currency"],
      });
  });
export type PayForm = z.infer<typeof payFormSchema>;

const toPayForm = (pay: PayRange | null): PayForm =>
  pay === null
    ? { min: "", max: "", currency: "EUR" }
    : { min: String(pay.min), max: String(pay.max), currency: pay.currency };
const fromPayForm = (pay: PayForm): PayRange | null =>
  pay.min.trim() === "" && pay.max.trim() === ""
    ? null
    : {
        min: Number(pay.min.trim()),
        max: Number(pay.max.trim()),
        currency: pay.currency,
      };

export const searchSchema = z.object({
  title_include: termListSchema,
  title_exclude: termListSchema,
  location: termListSchema,
  location_exclude: termListSchema,
  contract_types: z.array(z.enum(CONTRACT_TYPES)),
  remote_stance: z.enum(["", ...REMOTE_STANCES]),
  min_fit: z.number().int().min(0).max(100),
  days: z
    .string()
    .refine(
      (v) => YEAR_DAYS.test(v.trim()) && Number(v) >= 1 && Number(v) <= 365,
      "Enter a number of days between 1 and 365.",
    ),
  never_apply: z
    .array(
      z.object({
        value: text("a rule", NEVER_APPLY_LINE, { required: true }),
      }),
    )
    .max(NEVER_APPLY_MAX, `Keep at most ${NEVER_APPLY_MAX} rules.`),
  // Browser B8: edited by the browser sources card of this tab (browser-sources-card.tsx).
  browser_sources: z
    .array(browserSourceSchema)
    .max(BROWSER_SOURCES_MAX, `Keep at most ${BROWSER_SOURCES_MAX} sources.`),
  // Not edited here (Jobs owns it): carried through unchanged so a save never resets it.
  sweep_schedule: z.object({
    enabled: z.boolean(),
    days: z.array(z.enum(WEEKDAYS)),
    times: z.array(z.string()),
    timezone: z.string(),
  }),
  // No card edits it since documents D29 (written on a click only): saved as it was read.
  documents: z.object({
    auto: z.boolean(),
    min_fit: z.number().int().min(0).max(100),
    daily_ceiling: z
      .string()
      .refine(
        (v) =>
          CEILING.test(v.trim()) &&
          Number(v) >= 1 &&
          Number(v) <= DAILY_CEILING_MAX,
        DAILY_CEILING_PROBLEM,
      ),
  }),
  // O4: what the person aims for. A bonus to the fit, never a filter; the pay changes no score.
  seniority: z.array(z.enum(SENIORITIES)),
  industries: z
    .object(termListShape)
    .superRefine(checkTerms)
    .refine((list) => list.terms.length <= INDUSTRIES_MAX, {
      message: `Keep at most ${INDUSTRIES_MAX} industries.`,
      path: ["terms"],
    }),
  company_types: z.array(z.enum(COMPANY_TYPES)),
  compensation: z.object({
    salary: payFormSchema,
    daily_rate: payFormSchema,
  }),
});
export type SearchForm = z.infer<typeof searchSchema>;
export type BrowserSourceForm = SearchForm["browser_sources"][number];

export const toSearchForm = (search: Search): SearchForm => ({
  title_include: toTermList(search.title_include),
  title_exclude: toTermList(search.title_exclude),
  location: toTermList(search.location),
  location_exclude: toTermList(
    search.location_exclude ?? { advanced_regex: false, terms: [] },
  ),
  contract_types: CONTRACT_TYPES.filter((c) =>
    search.contract_types.includes(c),
  ),
  remote_stance: search.remote_stance ?? "",
  min_fit: search.min_fit,
  days: String(search.days),
  never_apply: search.never_apply.map((value) => ({ value })),
  // Only the protected kinds stay (owner, 2026-10-28); the api drops any other kind it holds.
  browser_sources: (search.browser_sources ?? [])
    .filter((s) => (BROWSER_KINDS as readonly string[]).includes(s.kind))
    .map((s) => ({
      kind: s.kind,
      query: s.query,
      location: s.location ?? "",
      enabled: s.enabled,
    })),
  sweep_schedule: {
    ...search.sweep_schedule,
    days: [...search.sweep_schedule.days],
    times: [...search.sweep_schedule.times],
  },
  documents: {
    ...search.documents,
    daily_ceiling: String(search.documents.daily_ceiling),
  },
  seniority: SENIORITIES.filter((s) => search.seniority.includes(s)),
  industries: toTermList(search.industries),
  company_types: COMPANY_TYPES.filter((c) => search.company_types.includes(c)),
  compensation: {
    salary: toPayForm(search.compensation.salary),
    daily_rate: toPayForm(search.compensation.daily_rate),
  },
});

export const fromSearchForm = (values: SearchForm): Search => ({
  title_include: fromTermList(values.title_include),
  title_exclude: fromTermList(values.title_exclude),
  location: fromTermList(values.location),
  location_exclude: fromTermList(values.location_exclude),
  contract_types: CONTRACT_TYPES.filter((c) =>
    values.contract_types.includes(c),
  ),
  remote_stance: values.remote_stance === "" ? null : values.remote_stance,
  min_fit: values.min_fit,
  days: Number(values.days.trim()),
  never_apply: values.never_apply.map((r) => cleanLine(r.value)),
  // Left out when empty: the api's default, so a section saved before B8 round trips as it was.
  ...(values.browser_sources.length > 0
    ? {
        browser_sources: values.browser_sources.map((s): BrowserSource => ({
          kind: s.kind,
          query: cleanLine(s.query),
          location: cleanLine(s.location) || null,
          enabled: s.enabled,
        })),
      }
    : {}),
  sweep_schedule: {
    ...values.sweep_schedule,
    days: [...values.sweep_schedule.days],
    times: [...values.sweep_schedule.times],
  },
  documents: {
    ...values.documents,
    daily_ceiling: Number(values.documents.daily_ceiling.trim()),
  },
  seniority: SENIORITIES.filter((s) => values.seniority.includes(s)),
  industries: fromTermList(values.industries),
  company_types: COMPANY_TYPES.filter((c) => values.company_types.includes(c)),
  compensation: {
    salary: fromPayForm(values.compensation.salary),
    daily_rate: fromPayForm(values.compensation.daily_rate),
  },
});
