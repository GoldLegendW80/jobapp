/**
 * What a template's page prints, gathered once from the text written and the facts of the profile
 * (templates.build_html's context): every layout of the page drawn here reads this.
 */
import type { Kind, Language } from "../api/documents-api";

import { listAt, MAX, textAt, type Draft } from "./edit-content";
import { problemsOf, textOf, type Form } from "./edit-form";
import type { ContactLine, PageFacts } from "./page-facts";
import { labelsOf, letterDate, periodOf, type PageLabels } from "./page-labels";

export interface SheetRole {
  key: string;
  organisation: string;
  title: string;
  location: string;
  period: string;
  bullets: string[];
  /** Its employer is not in the profile: the PDF leaves it out, the page edited here keeps it. */
  missing: boolean;
}

export interface SheetSchool {
  school: string;
  degree: string;
  location: string;
  period: string;
}

export const TEXT_PARTS = [
  "headline",
  "summary",
  "recipient",
  "subject",
  "salutation",
  "closing",
] as const;
export const LIST_PARTS = ["highlights", "competencies", "paragraphs"] as const;
export type TextPart = (typeof TEXT_PARTS)[number];
export type ListPart = (typeof LIST_PARTS)[number];
export type Part = TextPart | ListPart | "experience" | "skills";

export interface SheetPage {
  kind: Kind;
  language: Language;
  labels: PageLabels;
  date: string;
  name: string;
  contact: ContactLine[];
  /** The profile's photo, when the template has a place for it. */
  photo: string | null;
  roles: SheetRole[];
  /** Every employer the experience can name: the profile's, then the base CV's. */
  employers: PageFacts["employers"];
  schools: SheetSchool[];
  languages: { name: string; level: string | null }[];
  text: Record<TextPart, string>;
  lists: Record<ListPart, string[]>;
  skills: { label: string; items: string[] }[];
  /** The draft's form when the page is edited here; null for a version that is only read. */
  form: Form | null;
  /** The profile is read: an employer missing from it can be said. */
  known: boolean;
}

export function sheetPage({
  kind,
  language,
  content,
  facts,
  photo,
  form,
  known,
  now,
  ownLabels,
}: {
  kind: Kind;
  language: Language;
  content: Draft;
  facts: PageFacts;
  photo: string | null;
  form: Form | null;
  known: boolean;
  now?: Date;
  /** The template's own section labels in this language. */
  ownLabels?: Readonly<Record<string, string>>;
}): SheetPage {
  const labels = labelsOf(language, ownLabels);
  const record = (raw: unknown) => (raw ?? {}) as Record<string, unknown>;
  const roles = listAt(content, ["experience"])
    .map(record)
    .map((one) => {
      const key = textOf(one.employer_key);
      const fact = facts.employers.get(key);
      return {
        key,
        organisation: fact?.organisation ?? key,
        title: fact?.title ?? "",
        location: fact?.location ?? "",
        period: fact ? periodOf(fact.start, fact.end, labels) : "",
        bullets: listAt(one, ["bullets"]).map(textOf),
        missing: fact === undefined,
      };
    });
  return {
    kind,
    language,
    labels,
    date: letterDate(language, now),
    name: facts.name,
    contact: facts.contact,
    photo,
    roles,
    employers: facts.employers,
    schools: facts.schools.map((one) => ({
      school: one.school,
      degree: one.degree,
      location: one.location ?? "",
      period: periodOf(one.start, one.end, labels, false),
    })),
    languages: facts.languages,
    text: Object.fromEntries(
      TEXT_PARTS.map((part) => [part, textAt(content, [part])]),
    ) as Record<TextPart, string>,
    lists: Object.fromEntries(
      LIST_PARTS.map((part) => [part, listAt(content, [part]).map(textOf)]),
    ) as Record<ListPart, string[]>,
    skills: listAt(content, ["skills"])
      .map(record)
      .map((one) => ({
        label: textOf(one.label),
        items: listAt(one, ["items"])
          .map(textOf)
          .filter((term) => term !== ""),
      })),
    form,
    known,
  };
}

/** The roles the page prints: the PDF's (employers of the profile), and while edited, all. */
export function rolesShown(page: SheetPage): SheetRole[] {
  return page.form === null
    ? page.roles.filter((one) => !one.missing)
    : page.roles;
}

/** Whether a part has text to print. */
export function written(page: SheetPage, part: Part): boolean {
  if (part === "experience") return rolesShown(page).length > 0;
  if (part === "skills") return page.skills.length > 0;
  if ((LIST_PARTS as readonly string[]).includes(part))
    return page.lists[part as ListPart].some((one) => one.trim() !== "");
  return page.text[part as TextPart].trim() !== "";
}

/** Whether a part shows: written, or empty on a page edited here (where a click writes it). */
export function shows(page: SheetPage, part: Part): boolean {
  return page.form !== null || written(page, part);
}

/** What the page says of the roles whose employer left the profile. */
export function missingRoles(page: SheetPage): string[] {
  if (!page.known) return [];
  return page.roles
    .filter((one) => one.missing)
    .map(
      (one) =>
        `${one.key} is no longer in your profile: no title or dates print above its bullets.`,
    );
}

/** The initials of a name, as the portrait template's frame prints them (layout.initials). */
export function initialsOf(name: string): string {
  const letters = name
    .split(/\s+/)
    .map((word) => /\p{L}[̀-ͯ]*/u.exec(word)?.[0] ?? "")
    .filter((one) => one !== "");
  if (letters.length === 0) return "";
  const first = letters[0] ?? "";
  const last = letters.length > 1 ? (letters[letters.length - 1] ?? "") : "";
  return `${first}${last}`.toUpperCase();
}

/** How each part the person writes is named, edited and counted on the page. */
export const TEXTS: Record<
  TextPart,
  { label: string; empty: string; max: number; multiline?: boolean }
> = {
  headline: { label: "Headline", empty: "Add a headline", max: MAX.headline },
  summary: {
    label: "Summary",
    empty: "Add a summary",
    max: MAX.summary,
    multiline: true,
  },
  recipient: { label: "Recipient", empty: "Add the recipient", max: MAX.line },
  subject: { label: "Subject", empty: "Add a subject", max: MAX.line },
  salutation: {
    label: "Salutation",
    empty: "Add a salutation",
    max: MAX.line,
  },
  closing: { label: "Closing", empty: "Add a closing", max: MAX.line },
};

export const LISTS: Record<
  ListPart,
  {
    legend: string;
    noun: string;
    empty: string;
    max: number;
    itemMax: number;
    multiline?: boolean;
  }
> = {
  highlights: {
    legend: "Highlights",
    noun: "Highlight",
    empty: "Add highlights",
    max: MAX.highlights,
    itemMax: MAX.bullet,
  },
  competencies: {
    legend: "Competencies",
    noun: "Competency",
    empty: "Add competencies",
    max: MAX.competencies,
    itemMax: MAX.term,
  },
  paragraphs: {
    legend: "Paragraphs",
    noun: "Paragraph",
    empty: "Add a paragraph",
    max: MAX.paragraphs,
    itemMax: MAX.paragraph,
    multiline: true,
  },
};

export const isText = (part: Part): part is TextPart => part in TEXTS;
export const isList = (part: Part): part is ListPart => part in LISTS;

/** "the summary": what the button over a part says ("Edit the summary"). */
export function partName(part: Part): string {
  if (isText(part)) return `the ${TEXTS[part].label.toLowerCase()}`;
  if (isList(part)) return `the ${LISTS[part].legend.toLowerCase()}`;
  return `the ${part}`;
}

/** What a part says while it is closed: the notes of the fact check, what a save refused. */
export function partProblems(page: SheetPage, part: Part): string[] {
  const own = problemsOf(page.form, [[part]]);
  return part === "experience" ? [...missingRoles(page), ...own] : own;
}

/** The characters of a text as the templates count them (Python's `len`, code points). */
export function codePoints(text: string): number {
  return Array.from(text).length;
}

/**
 * The name's class in band, ledger and photo-columns: `long` past 22 characters (it may wrap
 * between its words), `longword` when a word is over 14 (it is set smaller).
 */
export function nameClass(name: string): string {
  const classes = ["name"];
  if (codePoints(name) > 22) classes.push("long");
  if (name.split(/\s+/).some((word) => codePoints(word) > 14))
    classes.push("longword");
  return classes.join(" ");
}
