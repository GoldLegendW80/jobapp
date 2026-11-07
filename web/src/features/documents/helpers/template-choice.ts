/**
 * What the template picker (template-panel.tsx) and the gallery (templates-page.tsx) share: the
 * order the person's templates come in, the names in a language, the gallery's filters, and the
 * sentences about what a switch changes (SPEC-documents.md, "Templates", "Switching", "Base CVs").
 * No dashes as separators in any sentence.
 */
import {
  type DocumentOut,
  type Kind,
  type Language,
  type VersionSummary,
} from "../api/documents-api";
import { type Template } from "../api/templates-api";
import { templateTagWord } from "@/lib/wording";

import { templateLabel } from "./labels";

/** The person's own templates ("From your CV") first, newest first as the api lists them, then the bundled ones. */
export function ordered(templates: readonly Template[]): Template[] {
  return [
    ...templates.filter((one) => one.user),
    ...templates.filter((one) => !one.user),
  ];
}

/** The language names and descriptions are shown in: the document's, else the browser's, else English. */
export function galleryLanguage(
  documentLanguage?: Language,
  browser: string = typeof navigator === "undefined" ? "" : navigator.language,
): Language {
  if (documentLanguage !== undefined) return documentLanguage;
  return browser.toLowerCase().split(/[-_]/)[0] === "fr" ? "fr" : "en";
}

export function nameOf(template: Template, language: Language): string {
  return (
    template.name[language] ?? template.name.en ?? templateLabel(template.slug)
  );
}

export function descriptionOf(template: Template, language: Language): string {
  return template.description[language] ?? template.description.en ?? "";
}

/** The page 1 image of a kind, as the api addresses it (the template version is in it), if any. */
export function thumbnailOf(
  template: Template,
  kind: Kind,
): string | undefined {
  const address = template.thumbnails[kind];
  return address === undefined || address === "" ? undefined : address;
}

export type PhotoFilter = "any" | "with" | "without";
export type ColumnsFilter = "any" | "one" | "two";

export interface Filters {
  photo: PhotoFilter;
  columns: ColumnsFilter;
  ats: boolean;
}

export const NO_FILTERS: Filters = { photo: "any", columns: "any", ats: false };

export const PHOTO_OPTIONS: { value: PhotoFilter; label: string }[] = [
  { value: "any", label: "Any" },
  { value: "with", label: "With a photo" },
  { value: "without", label: "No photo" },
];

export const COLUMNS_OPTIONS: { value: ColumnsFilter; label: string }[] = [
  { value: "any", label: "Any" },
  { value: "one", label: "One column" },
  { value: "two", label: "Two columns" },
];

/** A template's photo slot by its tags; the person's own carry no tags, so their manifest's `photo` says. */
function hasPhoto(template: Template): boolean {
  if (template.tags.includes("no photo")) return false;
  if (template.tags.includes("photo")) return true;
  return template.photo === "required" || template.photo === "optional";
}

/** Whether a template shows under the filters: it matches only what its manifest declares. */
export function matches(template: Template, filters: Filters): boolean {
  if (
    filters.photo !== "any" &&
    hasPhoto(template) !== (filters.photo === "with")
  )
    return false;
  if (
    filters.columns !== "any" &&
    !template.tags.includes(
      filters.columns === "one" ? "one column" : "two columns",
    )
  )
    return false;
  return !filters.ats || template.tags.includes("ats friendly");
}

/** What helps choose a template, in one line: "One column, with a photo, easy for hiring software to read". */
export function factsOf(template: Template): string {
  const facts: string[] = [];
  if (template.tags.includes("one column")) facts.push("one column");
  else if (template.tags.includes("two columns")) facts.push("two columns");
  facts.push(hasPhoto(template) ? "with a photo" : "no photo");
  if (template.tags.includes("ats friendly"))
    facts.push(templateTagWord("ats friendly").toLowerCase());
  const line = facts.join(", ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

export function isFiltered(filters: Filters): boolean {
  return (
    filters.photo !== NO_FILTERS.photo ||
    filters.columns !== NO_FILTERS.columns ||
    filters.ats !== NO_FILTERS.ats
  );
}

/** The page of the document the gallery was opened from. */
export function documentPath(document: DocumentOut): string {
  if (document.scope === "base") return `/profile/cv/${document.id}`;
  return document.posting_id === null
    ? "/profile/cv"
    : `/jobs/${document.posting_id}/documents/${document.kind}`;
}

/** The gallery, opened from a document so that it offers "Use for this document". */
export function galleryPath(documentId: string): string {
  return `/documents/templates?document=${encodeURIComponent(documentId)}`;
}

/** What a switch changes, and what it leaves as it is. */
export function inheritanceSentence(document: DocumentOut): string {
  if (document.scope === "base")
    return "The CVs you write from this one afterwards take its template. The CVs already written keep theirs.";
  return document.kind === "cv"
    ? "Only this CV changes. The base CV it was written from keeps its template."
    : "Only this letter changes. The CV of the posting keeps its template.";
}

/** The document's template is no longer served: the renderer prints it with the default. */
export function isRemoved(
  slug: string,
  templates: readonly Template[] | undefined,
): boolean {
  return templates !== undefined && !templates.some((one) => one.slug === slug);
}

export const REMOVED_SENTENCE =
  "The template this document used is no longer available, so it is printed with Classic. Choose another one to change it.";

/** A letter is never shortened and a base CV has no shortening pass: "Make it fit" is a posting's CV's. */
export function canShorten(document: DocumentOut): boolean {
  return document.scope === "posting" && document.kind === "cv";
}

/** The density step the draft's page starts from: the latest version's, when it has this template. */
export function draftStep(
  versions: readonly VersionSummary[] | undefined,
  slug: string,
): number | null {
  const latest = [...(versions ?? [])].sort((a, b) => b.number - a.number)[0];
  return latest?.template_slug === slug ? latest.fit_step : null;
}
