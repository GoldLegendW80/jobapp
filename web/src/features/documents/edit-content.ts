/**
 * The draft as the Edit view holds it, and the pure rules around it: the form's defaults, what a
 * save sends, where a note of the api lands, the template's counters, and what differs between
 * two drafts. The shapes are api/app/documents/content.py's; the api validates, this only fills.
 */
import { z } from "zod";

import { formatPeriod } from "@/features/profile/period";

import type { DocumentOut } from "./documents-api";
import { fieldLabel } from "./labels";

export type Draft = Record<string, unknown>;
export type Path = readonly (string | number)[];

/** The hard limits of content.py: an input never takes more. */
export const MAX = {
  line: 200,
  headline: 200,
  summary: 600,
  bullet: 300,
  term: 100,
  paragraph: 2000,
  notes: 2000,
  highlights: 3,
  competencies: 8,
  employers: 12,
  bullets: 8,
  skills: 4,
  skillItems: 30,
  paragraphs: 5,
  fixedEmployers: 30,
  fixedSchools: 20,
  fixedLanguages: 20,
} as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const pathKey = (path: Path): string => path.join(".");

/** The field a path is shown under: a skills group's terms are one line, so one field. */
export function fieldKey(path: Path): string {
  const key = pathKey(path);
  return /^skills\.\d+\.items\.\d+$/.test(key)
    ? key.replace(/\.\d+$/, "")
    : key;
}

export function getAt(value: unknown, path: Path): unknown {
  let here = value;
  for (const part of path) {
    if (Array.isArray(here) && typeof part === "number") here = here[part];
    else if (isRecord(here) && typeof part === "string") here = here[part];
    else return undefined;
  }
  return here;
}

export function setAt(value: unknown, path: Path, next: unknown): unknown {
  const [head, ...rest] = path;
  if (head === undefined) return next;
  if (typeof head === "number") {
    const list: unknown[] = Array.isArray(value)
      ? [...(value as unknown[])]
      : [];
    list[head] = setAt(list[head], rest, next);
    return list;
  }
  const record = isRecord(value) ? { ...value } : {};
  record[head] = setAt(record[head], rest, next);
  return record;
}

export function textAt(draft: Draft, path: Path): string {
  const value = getAt(draft, path);
  return typeof value === "string" ? value : "";
}

export function listAt(draft: Draft, path: Path): unknown[] {
  const value = getAt(draft, path);
  return Array.isArray(value) ? value : [];
}

/** Move the entry at `from` to `to` of a list; the list unchanged when either is outside it. */
export function moved<T>(list: readonly T[], from: number, to: number): T[] {
  if (from < 0 || to < 0 || from >= list.length || to >= list.length)
    return [...list];
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item as T);
  return next;
}

const CV = {
  target: "",
  headline: "",
  summary: "",
  highlights: [],
  competencies: [],
  experience: [],
  skills: [],
  notes: "",
};
const LETTER = {
  target: "",
  headline: "",
  recipient: "",
  subject: "",
  salutation: "",
  paragraphs: [],
  closing: "",
};
const FIXED = { employers: [], schools: [], languages: [] };

/** The draft with every field the form shows; a base CV also has its `fixed` block. */
export function withDefaults(document: DocumentOut): Draft {
  const draft = document.draft;
  if (document.kind === "letter") return { schema: 1, ...LETTER, ...draft };
  const cv: Draft = { schema: 1, ...CV, ...draft };
  if (document.scope !== "base") return cv;
  const fixed = isRecord(draft.fixed) ? draft.fixed : {};
  return { ...cv, fixed: { ...FIXED, ...fixed } };
}

/** An entry not typed yet: empty text, or a record of nothing but empty values. */
function blank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.every(blank);
  if (isRecord(value)) return Object.values(value).every(blank);
  return false;
}

function prune(value: unknown): unknown {
  if (Array.isArray(value))
    return value.filter((one) => !blank(one)).map(prune);
  if (isRecord(value))
    return Object.fromEntries(
      Object.entries(value).map(([key, one]) => [key, prune(one)]),
    );
  return value;
}

/** What a save sends: the draft without the list entries not typed yet (the api refuses them). */
export const toPayload = (draft: Draft): Draft => prune(draft) as Draft;

/** The form's path of a path the api names in the sent draft, whose blank entries were left out. */
export function localPath(draft: Draft, sent: string): Path {
  const out: (string | number)[] = [];
  let here: unknown = draft;
  for (const part of sent.split(".").filter((one) => one !== "")) {
    if (Array.isArray(here) && /^\d+$/.test(part)) {
      let kept = -1;
      let index = Number(part);
      for (let i = 0; i < here.length; i += 1) {
        if (blank(here[i])) continue;
        kept += 1;
        if (kept === Number(part)) {
          index = i;
          break;
        }
      }
      out.push(index);
      here = here[index];
    } else {
      out.push(/^\d+$/.test(part) ? Number(part) : part);
      here = isRecord(here) ? here[part] : undefined;
    }
  }
  return out;
}

/**
 * The key of every input the forms of edit-forms.tsx draw for `draft`: a refusal at any other key
 * has no field to be shown under. edit-panel.test.tsx compares it with the inputs drawn.
 */
export function fieldKeys(
  draft: Draft,
  letter: boolean,
  base: boolean,
): Set<string> {
  const keys = new Set<string>(
    letter
      ? ["target", "headline", "recipient", "subject", "salutation", "closing"]
      : ["target", "headline", "summary", "notes"],
  );
  const entries = (list: Path, fields: string[] = []) => {
    listAt(draft, list).forEach((_, index) => {
      const at = pathKey([...list, index]);
      if (fields.length === 0) keys.add(at);
      for (const field of fields) keys.add(`${at}.${field}`);
    });
  };
  if (letter) {
    entries(["paragraphs"]);
    return keys;
  }
  entries(["highlights"]);
  entries(["competencies"]);
  listAt(draft, ["experience"]).forEach((_, index) => {
    entries(["experience", index, "bullets"]);
  });
  entries(["skills"], ["label", "items"]);
  if (base) {
    const period = ["start", "end", "location"];
    entries(["fixed", "employers"], ["organisation", "title", ...period]);
    entries(["fixed", "schools"], ["school", "degree", ...period]);
    entries(["fixed", "languages"], ["name", "level"]);
  }
  return keys;
}

/** An employer of the fixed facts: the profile's, else the base CV's `fixed` block. */
export interface EmployerFact {
  key: string;
  organisation: string;
  title: string;
  start: string | null;
  end: string | null;
}

/** "March 2021 to today", "2019 to 2021"; empty without a start. */
export function span(start: string | null, end: string | null): string {
  const from = formatPeriod(start ?? "");
  if (from === null) return "";
  return `${from} to ${end ? (formatPeriod(end) ?? end) : "today"}`;
}

// --- notes --------------------------------------------------------------------------------------

const noteSchema = z.object({
  field: z.string(),
  start: z.number(),
  end: z.number(),
  code: z.string(),
  level: z.string(),
});

export interface Note {
  sentence: string;
  /** The words of the saved text the note is about; empty for the whole field. */
  excerpt: string;
}

const NOTES: Record<string, string> = {
  number_not_in_sources: "A number your profile does not state",
  percent_not_in_sources: "A percentage your profile does not state",
  amount_not_in_sources: "An amount your profile does not state",
  time_not_in_sources: "A time your profile does not state",
  duration_not_in_profile: "A duration your profile does not state",
  year_not_in_sources: "A year your profile does not state",
  date_not_in_sources: "A date your profile does not state",
  date_not_in_profile: "A date your profile gives otherwise",
  decade_not_in_profile: "A span of years your profile does not state",
  credential_not_in_profile: "A degree or certificate not in your profile",
  seniority_not_in_profile:
    "A seniority your titles in the profile do not have",
  change_not_in_profile: "A change your profile does not state",
  title_not_in_profile: "A job title not in your profile",
  employer_not_in_profile: "An employer not in your profile",
  school_not_in_profile: "A school not in your profile",
  phone_not_in_profile: "A phone number not in your profile",
  email_not_in_profile: "An e-mail address not in your profile",
  link_not_in_profile: "A link not in your profile",
  banned_phrase: "A phrase you banned",
  dash_separator: "A dash used as a separator",
  over_soft_length: "Longer than this template suggests",
  required_empty: "The page needs this field",
  unknown_employer: "An employer of neither your profile nor the base CV",
};

/**
 * The api's notes on the saved draft, by the form's path: what each says, and the words it is
 * about. They are shown, never acted on: what the person typed stays as typed.
 */
export function notesByField(
  saved: DocumentOut,
  draft: Draft,
): Map<string, Note[]> {
  const out = new Map<string, Note[]>();
  for (const raw of saved.notes) {
    const parsed = noteSchema.safeParse(raw);
    if (!parsed.success) continue;
    const { field, start, end, code } = parsed.data;
    const sentAt = field
      .split(".")
      .filter((one) => one !== "")
      .map((one) => (/^\d+$/.test(one) ? Number(one) : one));
    const text = textAt(saved.draft, sentAt);
    const note: Note = {
      sentence: NOTES[code] ?? "A note of the fact check",
      excerpt: end > start ? text.slice(start, end) : "",
    };
    const key = fieldKey(localPath(draft, field));
    out.set(key, [...(out.get(key) ?? []), note]);
  }
  return out;
}

// --- counters -----------------------------------------------------------------------------------

export type SoftLimits = Record<string, number | null>;

/** The template's counter of a field (rules._soft_limit): the first employer is the main one. */
export function softLimit(
  limits: SoftLimits | undefined,
  path: Path,
): number | null {
  if (limits === undefined) return null;
  const [head, index] = path;
  const name =
    head === "experience"
      ? index === 0
        ? "main_bullet"
        : "bullet"
      : head === "highlights"
        ? "highlight"
        : head === "competencies"
          ? "competency"
          : head === "paragraphs"
            ? "paragraph"
            : String(head);
  return limits[name] ?? null;
}

/** Characters as content._tidy leaves them: NFC, space runs as one, lines trimmed, by code point. */
export const characters = (text: string): number =>
  Array.from(
    text
      .normalize("NFC")
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+/g, " ")
      .split("\n")
      .map((line) => line.trim())
      .join("\n")
      .trim(),
  ).length;

// --- two drafts ---------------------------------------------------------------------------------

function texts(value: unknown, path: string, out: Map<string, string>) {
  if (typeof value === "string") out.set(path, value);
  else if (Array.isArray(value))
    value.forEach((one, i) => {
      texts(one, path === "" ? String(i) : `${path}.${String(i)}`, out);
    });
  else if (isRecord(value))
    for (const [key, one] of Object.entries(value))
      if (key !== "schema")
        texts(one, path === "" ? key : `${path}.${key}`, out);
}

export interface Difference {
  label: string;
  mine: string;
  theirs: string;
}

/** Every text that differs between two drafts, in the order of the other one, then mine. */
export function differences(mine: Draft, theirs: Draft): Difference[] {
  const a = new Map<string, string>();
  const b = new Map<string, string>();
  texts(toPayload(mine), "", a);
  texts(toPayload(theirs), "", b);
  const paths = [...b.keys(), ...[...a.keys()].filter((one) => !b.has(one))];
  return paths
    .filter((one) => (a.get(one) ?? "").trim() !== (b.get(one) ?? "").trim())
    .map((one) => ({
      label: fieldLabel(one),
      mine: a.get(one) ?? "",
      theirs: b.get(one) ?? "",
    }));
}
