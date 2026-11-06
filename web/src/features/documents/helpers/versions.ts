/** The version history of a document, as the See view reads it. */
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
