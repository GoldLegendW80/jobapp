/**
 * The checks the Import dialog runs on a chosen file before anything is sent, in the order the api
 * runs them (schemas.py parse_document): size, JSON, format, version, then the shape of the
 * document (six sections, each an object, and preferences when present; an optional photo). The api validates every field of
 * every section again on import and answers 422 with the first problem; this side only catches
 * the files that are plainly not a profile export, and describes the ones that are.
 */
import {
  EXPORT_FORMAT,
  EXPORT_VERSION,
  IMPORT_MAX_BYTES,
  SECTIONS,
  readSection,
  type Profile,
  type SectionName,
} from "./api";
import { completenessOf, type CompletenessResult } from "./completeness";
import { sameData } from "./diff";
import { SECTION_LABELS } from "./labels";

/** Top level keys the api accepts (ExportDocument, ImportIn). */
const KEYS = new Set([
  "format",
  "version",
  "exported_at",
  "sections",
  "photo",
  "base_revision",
]);
const PHOTO_MIMES = new Set(["image/png", "image/jpeg"]);
const PHOTO_MAX_BYTES = 2 * 1024 * 1024;

export interface SectionChange {
  name: SectionName;
  label: string;
  /** what the file holds, in a few words ("3 employers, 2 schools") */
  detail: string;
  /** false when the file holds exactly what the profile holds now */
  changed: boolean;
}

export interface ImportSummary {
  exportedAt: string | undefined;
  sections: SectionChange[];
  /** the file carries a photo, which replaces the current one */
  photo: boolean;
  completeness: CompletenessResult;
}

export type ImportCheck =
  | {
      ok: true;
      /** the document to send, without any base_revision of the file */
      document: Record<string, unknown>;
      summary: ImportSummary;
    }
  | { ok: false; error: string };

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const plural = (count: number, one: string, many = `${one}s`) =>
  `${String(count)} ${count === 1 ? one : many}`;

const count = (value: unknown) => (Array.isArray(value) ? value.length : 0);

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The preferences row of a file written before preferences existed: the file wins, so the
 * import sets the language and tone of documents back to their defaults. */
export const OLDER_FILE_PREFERENCES =
  "Not in this older file: the import resets the documents' language and tone to the defaults (auto, plain)";

/** A few words on what a section of the file holds. */
export function describeSection(name: SectionName, data: unknown): string {
  const s = isObject(data) ? data : {};
  switch (name) {
    case "identity": {
      const who =
        typeof s.full_name === "string" && s.full_name.trim() !== ""
          ? s.full_name.trim()
          : "No name";
      return `${who}, ${plural(count(s.languages), "language")}`;
    }
    case "experience":
      return `${plural(count(s.employers), "employer")}, ${plural(count(s.education), "school")}`;
    case "facts":
      return plural(count(s.blocks), "block");
    case "skills": {
      const categories = count(s.have) + count(s.adjacent) + count(s.none);
      return `${plural(categories, "category", "categories")}, ${plural(count(s.gates), "gate")}`;
    }
    case "search": {
      // Every search runs the job websites ticked in Find jobs (owner, 2026-10-28): boards or
      // feeds an older file holds are dropped by the api on import, so they are not described.
      const sites = Array.isArray(s.interactive_sites)
        ? s.interactive_sites
        : [];
      const ticked = sites.filter(
        (site) => isObject(site) && site.enabled === true,
      ).length;
      return `${plural(sites.length, "job website")}, ${String(ticked)} ticked`;
    }
    case "answers":
      return plural(count(s.work_authorization), "country", "countries");
    case "preferences": {
      const language =
        typeof s.documents_language === "string"
          ? s.documents_language
          : "auto";
      const tone =
        typeof s.documents_tone === "string" ? s.documents_tone : "plain";
      return `Documents: language ${language}, tone ${tone}`;
    }
  }
}

/**
 * Check a chosen file: its text and its size in bytes. `profile` is the current profile, to say
 * which sections the import would change.
 */
export function checkImportFile(
  text: string,
  bytes: number,
  profile: Profile,
): ImportCheck {
  if (bytes === 0) return { ok: false, error: "The file is empty." };
  if (bytes > IMPORT_MAX_BYTES) {
    return {
      ok: false,
      error: `The file is ${formatBytes(bytes)}; an import file is at most 2 MB.`,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, error: "The file is not valid JSON." };
  }
  if (!isObject(parsed)) {
    return { ok: false, error: "The file must hold one JSON object." };
  }
  if (parsed.format !== EXPORT_FORMAT) {
    return {
      ok: false,
      error: `Unknown file format: expected "format": "${EXPORT_FORMAT}", a profile export.`,
    };
  }
  if (parsed.version !== EXPORT_VERSION) {
    const newer =
      typeof parsed.version === "number" &&
      Number.isInteger(parsed.version) &&
      parsed.version > EXPORT_VERSION;
    return {
      ok: false,
      error: newer
        ? `Unsupported export version ${String(parsed.version)}: this app reads version ${String(EXPORT_VERSION)}. The file comes from a newer version of the app.`
        : `Unsupported export version: this app reads version ${String(EXPORT_VERSION)}.`,
    };
  }
  const unknown = Object.keys(parsed).filter((key) => !KEYS.has(key));
  if (unknown.length > 0) {
    return {
      ok: false,
      error: `Unknown field in the file: ${unknown.join(", ")}.`,
    };
  }
  const exportedAt = parsed.exported_at;
  if (typeof exportedAt !== "string" || Number.isNaN(Date.parse(exportedAt))) {
    return {
      ok: false,
      error: "The file has no valid export date (exported_at).",
    };
  }
  const sections = parsed.sections;
  if (!isObject(sections)) {
    return { ok: false, error: "The file has no sections." };
  }
  // Preferences came after version 1 files were first written: an older file has none (the api
  // reads its defaults then).
  const missing = SECTIONS.filter(
    (name) => !(name in sections) && name !== "preferences",
  );
  if (missing.length > 0) {
    return {
      ok: false,
      error: `The file lacks ${missing.length === 1 ? "the section" : "the sections"} ${missing.map((n) => SECTION_LABELS[n]).join(", ")}.`,
    };
  }
  const extra = Object.keys(sections).filter(
    (name) => !(SECTIONS as readonly string[]).includes(name),
  );
  if (extra.length > 0) {
    return {
      ok: false,
      error: `Unknown section in the file: ${extra.join(", ")}.`,
    };
  }
  const notObject = SECTIONS.filter(
    (name) => name in sections && !isObject(sections[name]),
  );
  if (notObject.length > 0) {
    return {
      ok: false,
      error: `${notObject.map((n) => SECTION_LABELS[n]).join(", ")} must be ${notObject.length === 1 ? "an object" : "objects"} in the file.`,
    };
  }
  const photo = parsed.photo;
  if (photo !== undefined && photo !== null) {
    const problem = photoProblem(photo);
    if (problem) return { ok: false, error: problem };
  }

  const document: Record<string, unknown> = { ...parsed };
  delete document.base_revision;
  const changes = SECTIONS.map((name) => ({
    name,
    label: SECTION_LABELS[name],
    detail:
      name === "preferences" && !("preferences" in sections)
        ? OLDER_FILE_PREFERENCES
        : describeSection(name, sections[name]),
    changed: !sameData(
      {
        ...(readSection(
          name,
          (sections[name] as object | undefined) ?? {},
        ) as object),
        // An import keeps the stored onboarding dates (O4): they never count as a change.
        ...(name === "preferences"
          ? { onboarding: profile.sections.preferences.onboarding }
          : {}),
      },
      profile.sections[name],
    ),
  }));
  return {
    ok: true,
    document,
    summary: {
      exportedAt,
      sections: changes,
      photo: photo !== undefined && photo !== null,
      completeness: completenessOf(sections),
    },
  };
}

function photoProblem(photo: unknown): string | undefined {
  if (!isObject(photo)) return "The photo in the file is not readable.";
  if (typeof photo.mime !== "string" || !PHOTO_MIMES.has(photo.mime))
    return "The photo in the file must be a PNG or JPEG image.";
  if (typeof photo.base64 !== "string" || photo.base64 === "")
    return "The photo in the file is empty.";
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(photo.base64) || photo.base64.length % 4)
    return "The photo in the file is not valid base64.";
  const decoded =
    (photo.base64.length / 4) * 3 -
    (photo.base64.match(/=+$/)?.[0].length ?? 0);
  if (decoded > PHOTO_MAX_BYTES)
    return "The photo in the file is larger than 2 MB.";
  return undefined;
}

/** The file name of a download, like the api's: profile-YYYY-MM-DD.json (UTC). */
export function exportFileName(exportedAt: unknown, now = new Date()): string {
  const date =
    typeof exportedAt === "string" && !Number.isNaN(Date.parse(exportedAt))
      ? new Date(exportedAt)
      : now;
  return `profile-${date.toISOString().slice(0, 10)}.json`;
}
