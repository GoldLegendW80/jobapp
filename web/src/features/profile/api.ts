/**
 * Calls of /api/profile (SPEC-profile.md, api/app/profile/router.py). Every save sends the
 * revision the page loaded (`base_revision`); a stale one answers 409 with the current revision
 * and sections, which `conflictOf` reads. Section types mirror api/app/profile/schemas.py.
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

export const SECTIONS = [
  "identity",
  "experience",
  "facts",
  "skills",
  "search",
  "answers",
  "preferences",
] as const;
export type SectionName = (typeof SECTIONS)[number];
/** The sections with a tab of their own; preferences is a card of the Search tab (O4). */
export const TAB_SECTIONS = [
  "identity",
  "experience",
  "facts",
  "skills",
  "search",
  "answers",
] as const satisfies readonly SectionName[];
export type TabSection = (typeof TAB_SECTIONS)[number];

// --- section shapes, as the api stores them ---------------------------------------------------

export interface Language {
  name: string;
  level: string | null;
}
export interface Link {
  label: string;
  url: string;
}
export interface Identity {
  full_name: string | null;
  headline: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  country: string | null;
  linkedin_url: string | null;
  website: string | null;
  links: Link[];
  languages: Language[];
}

export const EMPLOYER_CONTRACTS = [
  "CDI",
  "CDD",
  "apprenticeship",
  "freelance",
  "internship",
  "other",
] as const;
export type EmployerContract = (typeof EMPLOYER_CONTRACTS)[number];

export interface Employer {
  key: string;
  organisation: string;
  title: string;
  start: string;
  end: string | null;
  location: string | null;
  contract: EmployerContract | null;
}
export interface School {
  school: string;
  degree: string;
  start: string | null;
  end: string | null;
  location: string | null;
  details: string | null;
}
export interface Experience {
  employers: Employer[];
  education: School[];
}

export interface FactBlock {
  title: string;
  body: string;
}
export interface Facts {
  blocks: FactBlock[];
}

export interface TermList {
  advanced_regex: boolean;
  terms: string[];
}
export interface Category extends TermList {
  name: string;
}
export interface Gate extends Category {
  penalty: number;
}
export interface Skills {
  years_experience: number | null;
  have: Category[];
  adjacent: Category[];
  none: Category[];
  gates: Gate[];
}

export const CONTRACT_TYPES = [
  "CDI",
  "CDD",
  "VIE",
  "Stage",
  "Alternance",
  "Freelance",
] as const;
export const REMOTE_STANCES = ["remote", "hybrid", "office", "any"] as const;
// Opportunities O4, as api/app/profile/schemas.py names them.
export const SENIORITIES = [
  "intern",
  "junior",
  "mid",
  "senior",
  "lead",
  "principal",
] as const;
export const COMPANY_TYPES = [
  "startup",
  "scaleup",
  "large_company",
  "public_sector",
  "nonprofit",
  "agency",
  "consulting",
] as const;
export const INDUSTRIES_MAX = 30;
export const PAY_MAX = 10_000_000;
export const CURRENCIES = [
  "AED", "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
  "HKD", "HUF", "ILS", "INR", "JPY", "KRW", "MAD", "MXN", "NOK", "NZD", "PLN",
  "RON", "SAR", "SEK", "SGD", "THB", "TND", "TRY", "TWD", "USD", "ZAR",
] as const; // prettier-ignore
export const DOCUMENTS_LANGUAGES = ["auto", "en", "fr"] as const;
export const DOCUMENTS_TONES = ["plain", "formal", "warm"] as const;
export const EEO_TOPICS = [
  "gender",
  "ethnicity",
  "race",
  "disability",
  "veteran",
  "sexual_orientation",
  "age",
] as const;
export const EEO_POLICIES = ["decline", "leave", "stored"] as const;
export const SALARY_STANCES = ["leave_blank", "range", "sentence"] as const;
export const SALARY_PERIODS = ["year", "month", "day"] as const;

export const WEEKDAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
] as const;
export type Weekday = (typeof WEEKDAYS)[number];

/** schemas.py SweepSchedule: off by default, every day at 06:17 Europe/Paris. */
export interface SweepSchedule {
  enabled: boolean;
  days: Weekday[];
  times: string[];
  timezone: string;
}

/** schemas.py DocumentsGeneration (documents D10): the automatic generation of a posting's CV
 * and letter, on by default, at or above `min_fit`, at most `daily_ceiling` postings a day. */
export interface DocumentsGeneration {
  auto: boolean;
  min_fit: number;
  daily_ceiling: number;
}

/** The protected kinds of a browser source, consent rows owned by the browser api; any other
 * kind is dropped by the api on read and save (owner, 2026-10-28). */
export const BROWSER_KINDS = ["linkedin", "freework"] as const;
export interface BrowserSource {
  kind: (typeof BROWSER_KINDS)[number];
  query: string;
  location: string | null;
  enabled: boolean;
  /** Browser-owned intent: permission without activating the existing reader. */
  interactive_only?: boolean;
}
export const INTERACTIVE_PLATFORMS = [
  "linkedin",
  "welcome_to_the_jungle",
  "indeed",
  "freework",
  "custom",
] as const;
const interactiveSiteSchema = z.strictObject({
  platform: z.enum(INTERACTIVE_PLATFORMS),
  url: z
    .url()
    .trim()
    .min(1)
    .max(2000)
    .refine((value) => {
      try {
        const url = new URL(value);
        const host = url.hostname.replace(/\.$/, "");
        return (
          ["http:", "https:"].includes(url.protocol) &&
          !url.username &&
          !url.password &&
          !value.includes("\\") &&
          host.includes(".") &&
          host
            .split(".")
            .every((label) =>
              /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label),
            ) &&
          !/^\d+(?:\.\d+)*$/.test(host) &&
          !["localhost", "local", "internal", "lan", "home", "onion"].includes(
            host.split(".").at(-1) ?? "",
          )
        );
      } catch {
        return false;
      }
    }, "Enter a public HTTP or HTTPS website without sign-in details."),
  enabled: z.boolean(),
  mode: z.literal("llm_browser"),
});
export type InteractiveSite = z.infer<typeof interactiveSiteSchema>;
export const interactiveSettingsSchema = z
  .object({
    interactive_sites: z
      .array(interactiveSiteSchema)
      .max(10, "Choose at most 10 websites."),
    interactive_instructions: z
      .string()
      .refine(
        (value) =>
          !Array.from(value).some((character) => {
            const code = character.charCodeAt(0);
            return (code < 32 && ![9, 10, 13].includes(code)) || code === 127;
          }),
        "Remove control characters from the instructions.",
      )
      .transform((value) =>
        value.replace(/\r\n?/g, "\n").normalize("NFC").trim(),
      )
      .pipe(z.string().max(2000, "Keep instructions within 2,000 characters.")),
  })
  .superRefine((settings, context) => {
    const seen = new Set<string>();
    settings.interactive_sites.forEach((site, index) => {
      let key = site.url;
      try {
        const url = new URL(key);
        url.hash = "";
        url.hostname = url.hostname.replace(/\.$/, "");
        key = url.href;
      } catch {
        /* The field reports an invalid URL. */
      }
      if (seen.has(key))
        context.addIssue({
          code: "custom",
          path: ["interactive_sites", index, "url"],
          message: "This website is already selected.",
        });
      seen.add(key);
    });
  });
export interface Search {
  title_include: TermList;
  title_exclude: TermList;
  location: TermList;
  /** the unwanted places; absent from a search saved before them, read as an empty list */
  location_exclude?: TermList;
  contract_types: string[];
  remote_stance: (typeof REMOTE_STANCES)[number] | null;
  min_fit: number;
  days: number;
  never_apply: string[];
  /** the protected LinkedIn and Free-Work consent rows, set in Find jobs */
  browser_sources?: BrowserSource[];
  /** Saved intent; browser consent remains owned by the browser API. */
  interactive_sites?: InteractiveSite[];
  interactive_instructions?: string | null;
  /** Company job boards (owner, 2026-10-30), edited in Find jobs; null or absent: never saved */
  company_boards?: CompanyBoard[] | null;
  /** when the search runs automatically; edited in Jobs (J5), kept as is by this tab */
  sweep_schedule: SweepSchedule;
  /** the automatic generation of documents (D10), edited by the Search tab's documents card */
  documents: DocumentsGeneration;
  /** O4: a bonus to the fit when a posting matches, never a filter */
  seniority: (typeof SENIORITIES)[number][];
  industries: TermList;
  company_types: (typeof COMPANY_TYPES)[number][];
  /** O4: the pay sought; changes no score */
  compensation: { salary: PayRange | null; daily_rate: PayRange | null };
}

/** schemas.py CompanyBoard: a company's public job board on its ATS. */
export interface CompanyBoard {
  company: string;
  ats:
    | "ashby"
    | "greenhouse"
    | "lever"
    | "smartrecruiters"
    | "workable"
    | "recruitee";
  board: string;
  enabled: boolean;
}

export interface PayRange {
  min: number;
  max: number;
  currency: string;
}

export interface Preferences {
  /** written by the first login wizard only (O5); a save sends it back unchanged */
  onboarding: {
    completed_at: string | null;
    dismissed_at: string | null;
    /** the wizard's steps passed (Next or Skip), so another device resumes there */
    steps_passed: string[];
  };
  documents_language: (typeof DOCUMENTS_LANGUAGES)[number];
  documents_tone: (typeof DOCUMENTS_TONES)[number];
}

export interface WorkAuthorization {
  country: string;
  answer: string;
  needs_sponsorship: boolean | null;
}
export interface Answers {
  work_authorization: WorkAuthorization[];
  notice_period: string | null;
  earliest_start: string | null;
  salary_policy: string | null;
  relocation: string | null;
  travel: string | null;
  motivation: string | null;
  heard_about: string | null;
  leave_blank: string[];
  /** the optional fields the apply module reads (A3) */
  preferred_name: string | null;
  pronouns: string | null;
  years_experience_stated: string | null;
  highest_degree: string | null;
  highest_degree_year: number | null;
  willing_onsite: boolean | null;
  willing_hybrid: boolean | null;
  previously_applied_default: boolean | null;
  background_check_consent: boolean | null;
  reference_check_consent: boolean | null;
  eeo_policy: (typeof EEO_POLICIES)[number];
  eeo_answers: { topic: (typeof EEO_TOPICS)[number]; answer: string }[];
  /** null reads as "sentence" when salary_policy is filled, else "leave_blank" */
  salary_stance: (typeof SALARY_STANCES)[number] | null;
  salary_ranges: SalaryRange[];
}

export interface SalaryRange {
  currency: string;
  minimum: number;
  maximum: number | null;
  period: (typeof SALARY_PERIODS)[number];
}

export interface SectionData {
  identity: Identity;
  experience: Experience;
  facts: Facts;
  skills: Skills;
  search: Search;
  answers: Answers;
  preferences: Preferences;
}

/** The empty form of each section: what the api's models default to, for a section never saved. */
export const EMPTY: SectionData = {
  identity: {
    full_name: null,
    headline: null,
    email: null,
    phone: null,
    city: null,
    country: null,
    linkedin_url: null,
    website: null,
    links: [],
    languages: [],
  },
  experience: { employers: [], education: [] },
  facts: { blocks: [] },
  skills: {
    years_experience: null,
    have: [],
    adjacent: [],
    none: [],
    gates: [],
  },
  search: {
    title_include: { advanced_regex: false, terms: [] },
    title_exclude: { advanced_regex: false, terms: [] },
    location: { advanced_regex: false, terms: [] },
    location_exclude: { advanced_regex: false, terms: [] },
    contract_types: [],
    remote_stance: null,
    min_fit: 70,
    days: 30,
    never_apply: [],
    sweep_schedule: {
      enabled: false,
      days: [...WEEKDAYS],
      times: ["06:17"],
      timezone: "Europe/Paris",
    },
    documents: { auto: true, min_fit: 70, daily_ceiling: 100 },
    seniority: [],
    industries: { advanced_regex: false, terms: [] },
    company_types: [],
    compensation: { salary: null, daily_rate: null },
  },
  answers: {
    work_authorization: [],
    notice_period: null,
    earliest_start: null,
    salary_policy: null,
    relocation: null,
    travel: null,
    motivation: null,
    heard_about: null,
    leave_blank: [],
    preferred_name: null,
    pronouns: null,
    years_experience_stated: null,
    highest_degree: null,
    highest_degree_year: null,
    willing_onsite: null,
    willing_hybrid: null,
    previously_applied_default: null,
    background_check_consent: null,
    reference_check_consent: null,
    eeo_policy: "decline",
    eeo_answers: [],
    salary_stance: null,
    salary_ranges: [],
  },
  preferences: {
    onboarding: { completed_at: null, dismissed_at: null, steps_passed: [] },
    documents_language: "auto",
    documents_tone: "plain",
  },
};

// --- responses -------------------------------------------------------------------------------

const sectionsSchema = z.record(
  z.string(),
  z.record(z.string(), z.unknown()).nullable(),
);

const completenessSchema = z.object({
  overall: z.number().int().min(0).max(100),
  sections: z.record(
    z.string(),
    z.object({
      percent: z.number().int().min(0).max(100),
      missing: z.array(z.string()),
    }),
  ),
});
export type Completeness = z.infer<typeof completenessSchema>;

export const profileSchema = z.object({
  revision: z.number().int().nonnegative(),
  updated_at: z.string(),
  sections: sectionsSchema,
  completeness: completenessSchema,
  has_photo: z.boolean(),
  photo_etag: z.string().nullable(),
});
type RawProfile = z.infer<typeof profileSchema>;

export interface Profile extends Omit<RawProfile, "sections"> {
  /** Every section, a never saved one in its empty form. */
  sections: SectionData;
  /** Which sections were never saved (null on the api). */
  unsaved: SectionName[];
}

/** Sections of a response, each merged over its empty form (fields the api omits get defaults). */
export function readSections(raw: z.infer<typeof sectionsSchema>): {
  sections: SectionData;
  unsaved: SectionName[];
} {
  const sections = { ...EMPTY };
  const unsaved: SectionName[] = [];
  for (const name of SECTIONS) {
    const value = raw[name];
    if (value == null) {
      unsaved.push(name);
      continue;
    }
    // The api validated it on save; the merge only fills keys a newer model added.
    (sections as Record<SectionName, unknown>)[name] = readSection(name, value);
  }
  return { sections, unsaved };
}

/** One stored section as the app reads it: merged over its empty form, and the search's job
 * websites and instructions in their parsed form (a null instruction reads as ""). The Import
 * dialog reads a file's sections the same way, so an export imported back reads as unchanged. */
export function readSection(name: SectionName, value: object): unknown {
  const read: Record<string, unknown> = { ...EMPTY[name], ...value };
  if (
    name === "search" &&
    ("interactive_sites" in value || "interactive_instructions" in value)
  ) {
    const parsed = interactiveSettingsSchema.safeParse({
      interactive_sites: read.interactive_sites ?? [],
      interactive_instructions: read.interactive_instructions ?? "",
    });
    if (parsed.success) Object.assign(read, parsed.data);
  }
  return read;
}

export function toProfile(raw: RawProfile): Profile {
  return { ...raw, ...readSections(raw.sections) };
}

export const conflictSchema = z.object({
  detail: z.string(),
  revision: z.number().int().nonnegative(),
  sections: sectionsSchema,
});
export interface Conflict {
  detail: string;
  revision: number;
  sections: SectionData;
}

/** The current state carried by a 409 answer, or undefined for any other error. */
export function conflictOf(error: unknown): Conflict | undefined {
  if (!isApiError(error) || error.kind !== "http" || error.status !== 409)
    return undefined;
  const parsed = conflictSchema.safeParse(error.body);
  if (!parsed.success) return undefined;
  return {
    detail: parsed.data.detail,
    revision: parsed.data.revision,
    sections: readSections(parsed.data.sections).sections,
  };
}

export const photoSchema = z.object({
  mime: z.enum(["image/png", "image/jpeg"]),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  size: z.number().int().positive(),
  etag: z.string().min(1),
  updated_at: z.string(),
});
export type StoredPhoto = z.infer<typeof photoSchema>;

// --- calls -----------------------------------------------------------------------------------

export const profileKey = ["profile"] as const;

export function getProfile(signal?: AbortSignal): Promise<Profile> {
  return request("/profile", {
    signal,
    validate: (data) => toProfile(profileSchema.parse(data)),
  });
}

export function saveSection<S extends SectionName>(
  section: S,
  baseRevision: number,
  data: SectionData[S],
): Promise<Profile> {
  return request(`/profile/${section}`, {
    method: "PUT",
    body: { base_revision: baseRevision, data },
    validate: (body) => toProfile(profileSchema.parse(body)),
  });
}

/** Upload the image bytes as the raw body (the api sniffs the type from them). */
export function uploadPhoto(image: Blob): Promise<StoredPhoto> {
  return request("/profile/photo", {
    method: "PUT",
    rawBody: image,
    // A 2 MB upload on a slow phone link needs longer than the default.
    timeoutMs: 30_000,
    validate: (body) => photoSchema.parse(body),
  });
}

export async function deletePhoto(): Promise<void> {
  await request("/profile/photo", { method: "DELETE" });
}

/** The stored photo's address; the ETag in the query busts the browser cache when it changes. */
export function photoUrl(etag: string): string {
  return `/api/profile/photo?v=${encodeURIComponent(etag.replaceAll('"', ""))}`;
}

// --- history ---------------------------------------------------------------------------------

export const revisionSummarySchema = z.object({
  revision: z.number().int().positive(),
  section: z.enum(SECTIONS),
  created_at: z.string(),
});
export type RevisionSummary = z.infer<typeof revisionSummarySchema>;

export const revisionPageSchema = z.object({
  items: z.array(revisionSummarySchema),
  next_before: z.number().int().positive().nullable(),
});
export type RevisionPage = z.infer<typeof revisionPageSchema>;

export const revisionSchema = revisionSummarySchema.extend({
  data: z.record(z.string(), z.unknown()),
});
export interface Revision extends RevisionSummary {
  /** The section as saved then, merged over its empty form like a loaded profile. */
  data: SectionData[SectionName];
}

/** Revisions per page of the History drawer. */
export const REVISIONS_PAGE = 20;

export const revisionsKey = (section: SectionName) =>
  ["profile", "revisions", section] as const;
export const revisionKey = (revision: number) =>
  ["profile", "revision", revision] as const;

/** One page of a section's saves, newest first; `before` (exclusive) pages back. */
export function listRevisions(
  section: SectionName,
  before?: number,
  signal?: AbortSignal,
): Promise<RevisionPage> {
  const query = new URLSearchParams({
    section,
    limit: String(REVISIONS_PAGE),
  });
  if (before !== undefined) query.set("before", String(before));
  return request(`/profile/revisions?${query.toString()}`, {
    signal,
    validate: (body) => revisionPageSchema.parse(body),
  });
}

export function getRevision(
  revision: number,
  signal?: AbortSignal,
): Promise<Revision> {
  return request(`/profile/revisions/${String(revision)}`, {
    signal,
    validate: (body) => {
      const parsed = revisionSchema.parse(body);
      return {
        ...parsed,
        data: {
          ...EMPTY[parsed.section],
          ...parsed.data,
        },
      };
    },
  });
}

// --- export and import -----------------------------------------------------------------------

/** What the api reads (schemas.py EXPORT_FORMAT, EXPORT_VERSION, IMPORT_MAX_BYTES). */
export const EXPORT_FORMAT = "jobapp-profile";
export const EXPORT_VERSION = 1;
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024;

/** The export file as the api wrote it (GET /api/profile/export), decoded. */
export function exportProfile(): Promise<Record<string, unknown>> {
  return request("/profile/export", {
    // With a 2 MB photo inside, a phone link needs longer than the default.
    timeoutMs: 30_000,
    validate: (body) => z.record(z.string(), z.unknown()).parse(body),
  });
}

/** Replace the whole profile with an export file, based on the revision the page loaded. */
export function importProfile(
  document: Record<string, unknown>,
  baseRevision: number,
): Promise<Profile> {
  return request("/profile/import", {
    method: "POST",
    body: { ...document, base_revision: baseRevision },
    timeoutMs: 30_000,
    validate: (body) => toProfile(profileSchema.parse(body)),
  });
}
