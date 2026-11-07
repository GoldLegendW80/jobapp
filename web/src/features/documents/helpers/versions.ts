/** The version history of a document, as the document view reads it. */
import {
  correctionSchema,
  type Correction,
  type Version,
  type VersionSummary,
} from "../api/documents-api";

/** The list can be a moment behind the document: the document's own latest is always shown. */
export function withLatest(
  latest: VersionSummary | null,
  versions: VersionSummary[],
): VersionSummary[] {
  return latest !== null &&
    !versions.some((one) => one.number === latest.number)
    ? [latest, ...versions]
    : versions;
}

/** The corrections of a version that read as one; another shape is left out. */
export function correctionsOf(version: Version | undefined): Correction[] {
  return (version?.corrections ?? []).flatMap((one) => {
    const parsed = correctionSchema.safeParse(one);
    return parsed.success ? [parsed.data] : [];
  });
}

/** "Version 2, the latest", or for a document with no version yet, what it is. */
export function versionTitle(
  version: VersionSummary | undefined,
  isLatest: boolean,
): string {
  if (version === undefined) return "First draft";
  return `Version ${String(version.number)}${isLatest ? ", the latest" : ""}`;
}
