/**
 * Form values, zod schemas and conversions of the three editors (facts, skills, search). Like
 * forms.ts, the schemas mirror api/app/profile/schemas.py so most mistakes are caught before a
 * save: unique fact titles and the 200 KB cap of the fact base, term rules (plain terms at most
 * 200 characters; advanced ones compiled, at most 500 characters, no nested quantifiers; see
 * matching.ts), category names unique across have, adjacent and none, gate penalties 0 to 100,
 * board orgs written as the ATS spells them and unique per ATS. The api stays the judge: its 422
 * lands on the field, or the term, it names (errors.ts).
 */
import { z } from "zod";

import {
  ATS,
  COMPANY_TYPES,
  CONTRACT_TYPES,
  CURRENCIES,
  INDUSTRIES_MAX,
  PAY_MAX,
  REMOTE_STANCES,
  SENIORITIES,
  type Board,
  BROWSER_KINDS,
  type PayRange,
  type BrowserSource,
  type Category,
  type Facts,
  type Gate,
  type Search,
  type Skills,
  type TermList,
  WEEKDAYS,
} from "./api";
import { fold, LINE_MAX, text, uniqueBy } from "./forms";
import { cleanTerms, listProblems, termProblem } from "./matching";

export const FACTS_MAX_BYTES = 200_000;
export const FACT_BLOCKS_MAX = 200;
export const TERMS_MAX = 300;
export const CATEGORIES_MAX = 100;
export const GATES_MAX = 50;
export const BOARDS_MAX = 300;
export const NEVER_APPLY_MAX = 50;
export const NEVER_APPLY_LINE = 500;
/** schemas.py SearchSection.browser_sources: at most 10 sources. */
export const BROWSER_SOURCES_MAX = 10;
/** The kinds the sweep reads today (browser B8); `site` (B9) and `linkedin` (B10) come later. */
export const BROWSER_KINDS_RUNNABLE = ["google_careers", "vie"] as const;
export const ORG_MAX = 100;
export const ORG_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const encoder = new TextEncoder();
export const utf8Bytes = (value: string) => encoder.encode(value).length;

const cleanLine = (value: string) => value.normalize("NFC").trim();
const cleanText = (value: string) =>
  value.replace(/\r\n?/g, "\n").normalize("NFC").trim();

// --- facts -----------------------------------------------------------------------------------

/** Bytes of the fact base as the api counts them: UTF-8 of every cleaned title and body. */
export function factBytes(blocks: readonly { title: string; body: string }[]) {
  return blocks.reduce(
    (sum, b) =>
      sum + utf8Bytes(cleanLine(b.title)) + utf8Bytes(cleanText(b.body)),
    0,
  );
}

/** "12.3 KB of 200 KB", or exact bytes near and over the cap, where rounding would hide it. */
export function sizeText(bytes: number, cap: number): string {
  if (bytes >= cap * 0.99) {
    return `${bytes.toLocaleString("en")} of ${cap.toLocaleString("en")} bytes`;
  }
  return `${formatBytes(bytes)} of ${formatBytes(cap)}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} bytes`;
  return `${(bytes / 1000).toLocaleString("en", { maximumFractionDigits: 1 })} KB`;
}

export const factsSchema = z
  .object({
    blocks: z
      .array(
        z.object({
          title: text("a title", LINE_MAX, { required: true }),
          body: text("the text of this block", FACTS_MAX_BYTES, {
            required: true,
            multiline: true,
          }),
        }),
      )
      .max(FACT_BLOCKS_MAX, `Keep at most ${FACT_BLOCKS_MAX} blocks.`),
  })
  .superRefine((values, ctx) => {
    uniqueBy(
      values.blocks,
      (b) => fold(b.title),
      "title",
      "Another block already has this title.",
      ctx,
      "blocks",
    );
    const size = factBytes(values.blocks);
    if (size > FACTS_MAX_BYTES) {
      ctx.addIssue({
        code: "custom",
        message: `The fact base is ${size.toLocaleString("en")} bytes, at most ${FACTS_MAX_BYTES.toLocaleString("en")} allowed. Shorten or remove a block.`,
        path: ["blocks"],
      });
    }
  });
export type FactsForm = z.infer<typeof factsSchema>;

export const toFactsForm = (facts: Facts): FactsForm => ({
  blocks: facts.blocks.map((b) => ({ title: b.title, body: b.body })),
});

export const fromFactsForm = (values: FactsForm): Facts => ({
  blocks: values.blocks.map((b) => ({
    title: cleanLine(b.title),
    body: cleanText(b.body),
  })),
});

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
      .max(GATES_MAX, `Keep at most ${GATES_MAX} gates.`),
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
      "Another gate already has this name.",
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

// --- search ----------------------------------------------------------------------------------

/** Why an org would be refused, or undefined (schemas.py ORG_PATTERN, boards.py). */
export function orgProblem(value: string): string | undefined {
  const org = value.trim();
  if (org === "") return "Enter the org as it appears in the board address.";
  if (org.length > ORG_MAX) return `Use at most ${ORG_MAX} characters.`;
  if (!ORG_PATTERN.test(org) || org.includes("..")) {
    return "Use letters, digits, dots, dashes and underscores, starting with a letter or digit.";
  }
  return undefined;
}

const boardSchema = z.object({
  company: text("the company", LINE_MAX, { required: true }),
  ats: z.enum(ATS),
  org: z.string().superRefine((value, ctx) => {
    const problem = orgProblem(value);
    if (problem) ctx.addIssue({ code: "custom", message: problem });
  }),
  enabled: z.boolean(),
  extra_exclude: termListSchema,
});

export const boardKey = (b: { ats: string; org: string }) =>
  `${b.ats}:${b.org.trim().toLowerCase()}`;

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

export const searchSchema = z
  .object({
    title_include: termListSchema,
    title_exclude: termListSchema,
    location: termListSchema,
    contract_types: z.array(z.enum(CONTRACT_TYPES)),
    remote_stance: z.enum(["", ...REMOTE_STANCES]),
    min_fit: z.number().int().min(0).max(100),
    days: z
      .string()
      .refine(
        (v) => YEAR_DAYS.test(v.trim()) && Number(v) >= 1 && Number(v) <= 365,
        "Enter a number of days between 1 and 365.",
      ),
    boards: z
      .array(boardSchema)
      .max(BOARDS_MAX, `Keep at most ${BOARDS_MAX} boards.`),
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
    // Edited by the documents card of this tab (documents/generation-settings-card.tsx).
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
  })
  .superRefine((values, ctx) => {
    uniqueBy(
      values.boards,
      (b) => (b.org.trim() === "" ? "" : boardKey(b)),
      "org",
      "This board is already listed (same ATS and org).",
      ctx,
      "boards",
    );
  });
export type SearchForm = z.infer<typeof searchSchema>;
export type BoardForm = SearchForm["boards"][number];
export type BrowserSourceForm = SearchForm["browser_sources"][number];

export const toSearchForm = (search: Search): SearchForm => ({
  title_include: toTermList(search.title_include),
  title_exclude: toTermList(search.title_exclude),
  location: toTermList(search.location),
  contract_types: CONTRACT_TYPES.filter((c) =>
    search.contract_types.includes(c),
  ),
  remote_stance: search.remote_stance ?? "",
  min_fit: search.min_fit,
  days: String(search.days),
  boards: search.boards.map((b) => ({
    company: b.company,
    ats: b.ats,
    org: b.org,
    enabled: b.enabled,
    extra_exclude: toTermList(b.extra_exclude),
  })),
  never_apply: search.never_apply.map((value) => ({ value })),
  browser_sources: (search.browser_sources ?? []).map((s) => ({
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
  contract_types: CONTRACT_TYPES.filter((c) =>
    values.contract_types.includes(c),
  ),
  remote_stance: values.remote_stance === "" ? null : values.remote_stance,
  min_fit: values.min_fit,
  days: Number(values.days.trim()),
  boards: values.boards.map((b): Board => ({
    company: cleanLine(b.company),
    ats: b.ats,
    org: b.org.trim(),
    enabled: b.enabled,
    extra_exclude: fromTermList(b.extra_exclude),
  })),
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
