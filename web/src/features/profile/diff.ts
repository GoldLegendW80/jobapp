/**
 * The simple diffs of the History drawer: a past revision of a section against the current one.
 *
 * - Facts (Markdown) is text: `diffLines` gives the lines only in one version or the other, by a
 *   longest common subsequence after trimming the common head and tail.
 * - Every other section is a form: `flattenSection` turns it into labelled fields ("Employer 2 ›
 *   Title"), and `diffFields` pairs them by path and says which changed, appeared or went away.
 */
import type { SectionName } from "./api";

// --- text -------------------------------------------------------------------------------------

export type LineKind = "same" | "added" | "removed";
export interface LineOp {
  kind: LineKind;
  text: string;
}

/** Above this many cells the table would be slow: the middle is shown as removed then added. */
const LCS_CELLS_MAX = 4_000_000;

/**
 * The lines that turn `from` into `to`: "removed" lines are only in `from`, "added" lines only in
 * `to`, "same" lines in both. Within a change, removed lines come first.
 */
export function diffLines(from: string, to: string): LineOp[] {
  const a = from === "" ? [] : from.split("\n");
  const b = to === "" ? [] : to.split("\n");
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  )
    tail += 1;
  const same = (text: string): LineOp => ({ kind: "same", text });
  const ops: LineOp[] = a.slice(0, head).map(same);
  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);
  ops.push(...middle(midA, midB));
  ops.push(...a.slice(a.length - tail).map(same));
  return ops;
}

function middle(a: string[], b: string[]): LineOp[] {
  const removed = (text: string): LineOp => ({ kind: "removed", text });
  const added = (text: string): LineOp => ({ kind: "added", text });
  if (a.length === 0 || b.length === 0 || a.length * b.length > LCS_CELLS_MAX)
    return [...a.map(removed), ...b.map(added)];
  // lengths[i][j]: the longest common subsequence of a[i:] and b[j:]
  const width = b.length + 1;
  const lengths = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lengths[i * width + j] =
        a[i] === b[j]
          ? (lengths[(i + 1) * width + j + 1] ?? 0) + 1
          : Math.max(
              lengths[(i + 1) * width + j] ?? 0,
              lengths[i * width + j + 1] ?? 0,
            );
    }
  }
  const ops: LineOp[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const lineA = a[i] ?? "";
    const lineB = b[j] ?? "";
    if (lineA === lineB) {
      ops.push({ kind: "same", text: lineA });
      i += 1;
      j += 1;
    } else if (
      (lengths[(i + 1) * width + j] ?? 0) >= (lengths[i * width + j + 1] ?? 0)
    ) {
      ops.push(removed(lineA));
      i += 1;
    } else {
      ops.push(added(lineB));
      j += 1;
    }
  }
  ops.push(...a.slice(i).map(removed), ...b.slice(j).map(added));
  return ops;
}

/** The fact base as one Markdown text, each block under its title. */
export function factsText(data: unknown): string {
  const blocks = (data as { blocks?: unknown } | null)?.blocks;
  if (!Array.isArray(blocks)) return "";
  return blocks
    .map((block) => {
      const { title, body } = block as { title?: unknown; body?: unknown };
      return `# ${typeof title === "string" ? title : ""}\n\n${typeof body === "string" ? body : ""}`;
    })
    .join("\n\n");
}

// --- forms ------------------------------------------------------------------------------------

/** Names of the fields, as the editors label them. */
const FIELD_TITLES: Record<string, string> = {
  full_name: "Full name",
  headline: "Headline",
  email: "Email",
  phone: "Phone",
  city: "City",
  country: "Country",
  linkedin_url: "LinkedIn",
  website: "Website",
  label: "Label",
  url: "URL",
  name: "Name",
  level: "Level",
  key: "Key",
  organisation: "Organisation",
  title: "Title",
  start: "Start",
  end: "End",
  location: "Location",
  contract: "Contract",
  school: "School",
  degree: "Degree",
  details: "Details",
  years_experience: "Years of experience",
  terms: "Terms",
  penalty: "Penalty",
  title_include: "Job titles",
  title_exclude: "Unwanted titles",
  location_exclude: "Unwanted places",
  contract_types: "Contract types",
  remote_stance: "Remote stance",
  min_fit: "Minimum fit",
  days: "Posting age (days)",
  company: "Company",
  ats: "ATS",
  org: "Org",
  enabled: "Enabled",
  extra_exclude: "Extra exclude terms",
  never_apply: "Never apply",
  answer: "Answer",
  needs_sponsorship: "Needs sponsorship",
  notice_period: "Notice period",
  earliest_start: "Earliest start",
  salary_policy: "Salary policy",
  relocation: "Relocation",
  travel: "Travel",
  motivation: "Motivation",
  heard_about: "How you heard",
  leave_blank: "Leave blank",
  preferred_name: "Preferred name",
  pronouns: "Pronouns",
  years_experience_stated: "Years of experience as you state them",
  highest_degree: "Highest degree",
  highest_degree_year: "Year of the highest degree",
  willing_onsite: "Willing to work on site",
  willing_hybrid: "Willing to work hybrid",
  previously_applied_default: "Previously applied or worked there",
  background_check_consent: "Background check consent",
  reference_check_consent: "Reference check consent",
  eeo_policy: "Equal opportunity questions",
  eeo_answers: "Stored equal opportunity answers",
  salary_stance: "Salary stance",
  salary_ranges: "Salary ranges",
  topic: "Topic",
  currency: "Currency",
  minimum: "Minimum",
  maximum: "Maximum",
  period: "Per",
};

/** What one item of a list is called ("Employer 2"). */
const ITEM_TITLES: Record<string, string> = {
  links: "Link",
  languages: "Language",
  employers: "Employer",
  education: "School",
  blocks: "Block",
  have: "Have",
  adjacent: "Adjacent",
  none: "Lacks",
  gates: "Gate",
  boards: "Board",
  work_authorization: "Work authorisation",
  eeo_answers: "Equal opportunity answer",
  salary_ranges: "Salary range",
};

export interface FieldRow {
  /** stable key: the JSON path ("employers.1.title") */
  path: string;
  label: string;
  /** the value as text; "" for an empty field */
  value: string;
}

/** Titles that depend on the path, checked before FIELD_TITLES. */
const PATH_TITLES: Record<string, string> = {
  sweep_schedule: "Automatic search",
  "sweep_schedule.enabled": "On",
  "sweep_schedule.days": "Days",
  "sweep_schedule.times": "Times",
  "sweep_schedule.timezone": "Time zone",
};

const titleOf = (key: string) =>
  FIELD_TITLES[key] ??
  key.charAt(0).toUpperCase() + key.slice(1).replaceAll("_", " ");

const isTermList = (
  value: object,
): value is {
  terms: unknown[];
  advanced_regex: boolean;
} =>
  "terms" in value &&
  "advanced_regex" in value &&
  Array.isArray((value as { terms: unknown }).terms);

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return JSON.stringify(value);
}

function termsText(list: { terms: unknown[]; advanced_regex: boolean }) {
  const joined = list.terms.map(text).join(", ");
  return joined !== "" && list.advanced_regex
    ? `${joined} (regular expressions)`
    : joined;
}

function walk(value: unknown, path: string, label: string, out: FieldRow[]) {
  if (Array.isArray(value)) {
    const key = path.split(".").pop() ?? "";
    const item = ITEM_TITLES[key];
    if (item === undefined || value.every((v) => typeof v !== "object")) {
      out.push({ path, label, value: value.map(text).join(", ") });
      return;
    }
    if (value.length === 0) {
      out.push({ path, label, value: "" });
      return;
    }
    value.forEach((entry, index) => {
      walk(
        entry,
        `${path}.${String(index)}`,
        `${item} ${String(index + 1)}`,
        out,
      );
    });
    return;
  }
  if (typeof value === "object" && value !== null) {
    if (isTermList(value)) {
      const rest = Object.entries(value).filter(
        ([k]) => k !== "terms" && k !== "advanced_regex",
      );
      for (const [k, v] of rest)
        walk(v, `${path}.${k}`, join(label, titleOf(k)), out);
      const termsLabel =
        rest.length === 0 && label !== "" ? label : join(label, "Terms");
      out.push({
        path: `${path}.terms`,
        label: termsLabel,
        value: termsText(value),
      });
      return;
    }
    for (const [k, v] of Object.entries(value)) {
      const child = path === "" ? k : `${path}.${k}`;
      walk(v, child, join(label, PATH_TITLES[child] ?? titleOf(k)), out);
    }
    return;
  }
  out.push({ path, label, value: text(value) });
}

const join = (parent: string, child: string) =>
  parent === "" ? child : `${parent} › ${child}`;

/** A section as labelled fields, in the order the editor shows them. */
export function flattenSection(
  section: SectionName,
  data: unknown,
): FieldRow[] {
  if (section === "facts") {
    return [{ path: "facts", label: "Fact base", value: factsText(data) }];
  }
  const rows: FieldRow[] = [];
  walk(data ?? {}, "", "", rows);
  return rows;
}

export type FieldStatus = "same" | "changed" | "added" | "removed";
export interface FieldDiff {
  path: string;
  label: string;
  /** in the past revision; undefined when the field does not exist there */
  revision: string | undefined;
  /** in the current version; undefined when the field does not exist there */
  current: string | undefined;
  status: FieldStatus;
}

/**
 * The fields of a past revision against the current version, revision order first, then fields
 * only the current version has. "added": only in the revision (restoring brings it back);
 * "removed": only in the current version (restoring drops it). An empty field and a missing one
 * are the same.
 */
export function diffFields(
  revision: FieldRow[],
  current: FieldRow[],
): FieldDiff[] {
  const now = new Map(current.map((row) => [row.path, row]));
  const seen = new Set<string>();
  const out: FieldDiff[] = [];
  for (const row of revision) {
    seen.add(row.path);
    const other = now.get(row.path);
    const status: FieldStatus =
      other === undefined
        ? row.value === ""
          ? "same"
          : "added"
        : other.value === row.value
          ? "same"
          : "changed";
    out.push({
      path: row.path,
      label: row.label,
      revision: row.value,
      current: other?.value,
      status,
    });
  }
  for (const row of current) {
    if (seen.has(row.path)) continue;
    out.push({
      path: row.path,
      label: row.label,
      revision: undefined,
      current: row.value,
      status: row.value === "" ? "same" : "removed",
    });
  }
  return out;
}

/** Key order independent JSON, to tell whether two sections hold the same data. */
export function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableJson(v)}`).join(",")}}`;
  }
  if (value === undefined) return "null";
  return JSON.stringify(value);
}

export const sameData = (a: unknown, b: unknown) =>
  stableJson(a) === stableJson(b);
