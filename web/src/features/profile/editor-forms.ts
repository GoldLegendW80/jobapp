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
  CONTRACT_TYPES,
  REMOTE_STANCES,
  type Board,
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

const YEAR_DAYS = /^\d{1,3}$/;

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
    // Not edited here (Jobs owns it): carried through unchanged so a save never resets it.
    sweep_schedule: z.object({
      enabled: z.boolean(),
      days: z.array(z.enum(WEEKDAYS)),
      times: z.array(z.string()),
      timezone: z.string(),
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
  sweep_schedule: {
    ...search.sweep_schedule,
    days: [...search.sweep_schedule.days],
    times: [...search.sweep_schedule.times],
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
  sweep_schedule: {
    ...values.sweep_schedule,
    days: [...values.sweep_schedule.days],
    times: [...values.sweep_schedule.times],
  },
});
