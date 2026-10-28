/**
 * Completeness of a set of sections, ported from api/app/profile/schemas.py `completeness` so the
 * Import dialog can say what the profile will look like before anything is sent. The server stays
 * the judge: after an import the page shows its numbers.
 *
 * Per section, the share of required fields filled (a whole number, Python's round half to even),
 * and the required fields still missing; overall, the rounded mean of the six sections with required fields (preferences has
 * none: 100, left out of the mean). A section absent
 * from the input counts as empty.
 */
import { SECTIONS, type SectionName } from "./api";

type Section = Record<string, unknown>;
type Rule = (section: Section) => boolean;

const filled = (value: unknown): boolean => {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
};
const field =
  (name: string): Rule =>
  (s) =>
    filled(s[name]);
const terms =
  (name: string): Rule =>
  (s) => {
    const list = s[name] as { terms?: unknown } | null | undefined;
    return filled(list?.terms);
  };

/** The api's REQUIRED rules, in its order. */
export const REQUIRED: Record<SectionName, Record<string, Rule>> = {
  identity: Object.fromEntries(
    [
      "full_name",
      "headline",
      "email",
      "phone",
      "city",
      "country",
      "languages",
    ].map((name) => [name, field(name)]),
  ),
  experience: { employers: field("employers"), education: field("education") },
  facts: { blocks: field("blocks") },
  skills: {
    years_experience: (s) =>
      s.years_experience !== null && s.years_experience !== undefined,
    have: field("have"),
  },
  search: {
    title_include: terms("title_include"),
    location: terms("location"),
    contract_types: field("contract_types"),
    remote_stance: field("remote_stance"),
    // Every search runs the job websites ticked in Find jobs alone (owner, 2026-10-28): a saved
    // board no longer counts.
    interactive_sites: (s) =>
      Array.isArray(s.interactive_sites) &&
      s.interactive_sites.some(
        (site) =>
          typeof site === "object" &&
          site !== null &&
          (site as { enabled?: unknown }).enabled === true,
      ),
  },
  answers: Object.fromEntries(
    [
      "work_authorization",
      "notice_period",
      "earliest_start",
      "salary_policy",
      "motivation",
    ].map((name) => [name, field(name)]),
  ),
  // Every field has a default: always filled, and left out of the overall mean (O4).
  preferences: {},
};

/** Python's round(): halves go to the even neighbour (round(0.5) is 0, round(2.5) is 2). */
export function roundHalfEven(value: number): number {
  const floor = Math.floor(value);
  const fraction = value - floor;
  if (fraction > 0.5) return floor + 1;
  if (fraction < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

export interface SectionCompleteness {
  percent: number;
  missing: string[];
}
export interface CompletenessResult {
  overall: number;
  sections: Record<SectionName, SectionCompleteness>;
}

export function completenessOf(
  sections: Partial<Record<SectionName, unknown>>,
): CompletenessResult {
  const result = {} as Record<SectionName, SectionCompleteness>;
  for (const name of SECTIONS) {
    const raw = sections[name];
    const data =
      typeof raw === "object" && raw !== null && !Array.isArray(raw)
        ? (raw as Section)
        : {};
    const rules = Object.entries(REQUIRED[name]);
    const missing = rules.filter(([, ok]) => !ok(data)).map(([key]) => key);
    result[name] = {
      percent:
        rules.length === 0
          ? 100
          : roundHalfEven(
              (100 * (rules.length - missing.length)) / rules.length,
            ),
      missing,
    };
  }
  const counted = SECTIONS.filter(
    (name) => Object.keys(REQUIRED[name]).length > 0,
  );
  const total = counted.reduce((sum, name) => sum + result[name].percent, 0);
  return {
    overall: roundHalfEven(total / counted.length),
    sections: result,
  };
}
