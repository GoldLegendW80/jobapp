/**
 * One document of the documents module (SPEC-documents.md, "API"): the vocabulary, its shapes, its
 * draft, its versions and its generations. The other resources have their own file beside this
 * one: base CVs, a posting's documents, templates, spend and consent, chat, settings. Shapes
 * mirror the response models of api/app/documents/router.py and base_router.py.
 *
 * Page images and PDFs are fetched by the browser with the session cookie, so they are
 * addresses (`...Url`), not calls.
 */
import { z } from "zod";

import { API_BASE, errorDetail, isApiError } from "@/lib/api";

import { DOCUMENTS_ROOT, getJson, pathId, sendJson } from "./documents-request";

// --- vocabulary ---------------------------------------------------------------------------------

export const KINDS = ["cv", "letter"] as const;
export type Kind = (typeof KINDS)[number];
export const LANGUAGES = ["en", "fr"] as const;
export type Language = (typeof LANGUAGES)[number];
export const RENDER_STATES = ["idle", "queued", "rendering"] as const;
export type RenderState = (typeof RENDER_STATES)[number];
export const GENERATION_STATUSES = [
  "waiting",
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
] as const;
export type GenerationStatus = (typeof GENERATION_STATUSES)[number];
export const WAIT_REASONS = [
  "daily_ceiling",
  "consent",
  "credential",
  "base_cv",
] as const;
export type WaitReason = (typeof WAIT_REASONS)[number];
export const BASE_SOURCES = ["profile", "built", "uploaded"] as const;
export type BaseSource = (typeof BASE_SOURCES)[number];
export const ORIGINS = [
  "generated",
  "edited",
  "chat",
  "restored",
  "template",
  "uploaded",
  "legacy",
] as const;
export type Origin = (typeof ORIGINS)[number];
export type Channel = "content" | "design";

export const kindSchema = z.enum(KINDS);
export const languageSchema = z.enum(LANGUAGES);
export const renderStateSchema = z.enum(RENDER_STATES);
export const generationStatusSchema = z.enum(GENERATION_STATUSES);
export const waitReasonSchema = z.enum(WAIT_REASONS);
const originSchema = z.enum(ORIGINS);

// --- shapes -------------------------------------------------------------------------------------

const record = z.record(z.string(), z.unknown());

export const renderSchema = z.object({
  state: renderStateSchema,
  revision: z.number().nullable(),
  /** A code of the api's fixed list. */
  error: z.string().nullable(),
  /** What the person reads. */
  message: z.string().nullable(),
});
export type Render = z.infer<typeof renderSchema>;

export const versionSummarySchema = z.object({
  number: z.number(),
  origin: originSchema,
  created_at: z.string(),
  language: languageSchema,
  template_slug: z.string(),
  template_version: z.number(),
  fit: z.enum(["one_page", "overflow"]).nullable(),
  fit_step: z.number().nullable(),
  pages: z.number().nullable(),
  has_pdf: z.boolean(),
  /** page1.png draws it: false for the command line's own PDF of an imported version. */
  has_preview: z.boolean(),
  approved_at: z.string().nullable(),
  frozen_at: z.string().nullable(),
  frozen_reason: z
    .enum(["applied", "marked_sent", "apply", "legacy_applied"])
    .nullable(),
  corrections_count: z.number(),
  unprintable_count: z.number(),
});
export type VersionSummary = z.infer<typeof versionSummarySchema>;

/** One entry of the corrections record: the fact check changed `before` into `after`. */
export const correctionSchema = z.object({
  field_path: z.string(),
  before: z.string(),
  /** Empty for a removal. */
  after: z.string(),
  code: z.string(),
  by: z.enum(["writer", "code"]),
  reason: z.string(),
});
export type Correction = z.infer<typeof correctionSchema>;

export const versionSchema = versionSummarySchema.extend({
  content: record,
  corrections: z.array(record),
  unprintable: z.array(record),
});
export type Version = z.infer<typeof versionSchema>;

export const documentSchema = z.object({
  id: z.string(),
  scope: z.enum(["base", "posting"]),
  kind: kindSchema,
  posting_id: z.string().nullable(),
  base_document_id: z.string().nullable(),
  name: z.string().nullable(),
  language: languageSchema,
  template_slug: z.string(),
  draft: record,
  draft_revision: z.number(),
  draft_written_by: z.string(),
  draft_updated_at: z.string(),
  /** The checks of the draft at this revision. */
  notes: z.array(record),
  render: renderSchema,
  latest: versionSummarySchema.nullable(),
});
export type DocumentOut = z.infer<typeof documentSchema>;

export const generationSchema = z.object({
  id: z.string(),
  status: generationStatusSchema,
  wait_reason: waitReasonSchema.nullable(),
  /** A code of the api's fixed list. */
  error: z.string().nullable(),
});
export type Generation = z.infer<typeof generationSchema>;

export const generationDetailSchema = generationSchema.extend({
  document_id: z.string(),
  mode: z.string(),
  trigger: z.enum(["auto", "click", "chat"]),
  outcome: z.enum(["draft_replaced", "kept_as_version"]).nullable(),
  version_number: z.number().nullable(),
  corrections: z.number(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
});
export type GenerationDetail = z.infer<typeof generationDetailSchema>;

export const queuedSchema = z.object({ render: renderSchema });
export type Queued = z.infer<typeof queuedSchema>;

/** The body of a 409 on a draft save: the current document, for the "changed in another tab" view. */
export const staleDraftSchema = z.object({
  detail: z.string(),
  document: documentSchema,
});
export type StaleDraft = z.infer<typeof staleDraftSchema>;

// --- keys ---------------------------------------------------------------------------------------

export const documentKey = (id: string) =>
  ["documents", "document", id] as const;
export const versionsKey = (id: string) =>
  ["documents", "document", id, "versions"] as const;
export const versionKey = (id: string, number: number) =>
  ["documents", "document", id, "version", number] as const;
export const generationKey = (id: string) =>
  ["documents", "generation", id] as const;

// --- calls --------------------------------------------------------------------------------------

const documentPath = (documentId: string) =>
  `${DOCUMENTS_ROOT}/${pathId(documentId)}`;
const versionPath = (documentId: string, number: number) =>
  `${documentPath(documentId)}/versions/${pathId(number)}`;

// One document

export const getDocument = (
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentOut> =>
  getJson(documentPath(documentId), documentSchema, signal);

/** Save the draft; a stale `revision` answers 409 with the current document (`staleDraft`). */
export const saveDraft = (
  documentId: string,
  content: Record<string, unknown>,
  revision: number,
): Promise<DocumentOut> =>
  sendJson(
    `${documentPath(documentId)}/draft`,
    "PUT",
    documentSchema,
    content,
    {
      "If-Match": String(revision),
    },
  );

export const patchDocument = (
  documentId: string,
  change: { language?: Language; template_slug?: string },
): Promise<DocumentOut> =>
  sendJson(documentPath(documentId), "PATCH", documentSchema, change);

export const renderDocument = (documentId: string): Promise<Queued> =>
  sendJson(`${documentPath(documentId)}/render`, "POST", queuedSchema);

/** "Make it fit": a `shorten` generation. */
export const fitDocument = (documentId: string): Promise<Generation> =>
  sendJson(`${documentPath(documentId)}/fit`, "POST", generationSchema);

/** The current document inside a 409 of `saveDraft`, else undefined. */
export function staleDraft(error: unknown): StaleDraft | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const parsed = staleDraftSchema.safeParse(error.body);
  return parsed.success ? parsed.data : undefined;
}

// Design capture of an upload

export const captureDesign = (documentId: string): Promise<Generation> =>
  sendJson(
    `${documentPath(documentId)}/design/capture`,
    "POST",
    generationSchema,
  );

/** The original's page image (a PDF upload), rendered on demand; 404 for a DOCX. */
export const originalPageUrl = (documentId: string, page: number): string =>
  `${API_BASE}${documentPath(documentId)}/original?page=${String(page)}`;

// Versions

export const listVersions = (
  documentId: string,
  signal?: AbortSignal,
): Promise<VersionSummary[]> =>
  getJson(
    `${documentPath(documentId)}/versions`,
    z.array(versionSummarySchema),
    signal,
  );

export const getVersion = (
  documentId: string,
  number: number,
  signal?: AbortSignal,
): Promise<Version> =>
  getJson(versionPath(documentId, number), versionSchema, signal);

/** The PDF, served inline. */
export const versionPdfUrl = (documentId: string, number: number): string =>
  `${API_BASE}${versionPath(documentId, number)}/pdf`;

/** The preview image of page 1 (cards and phones); `width` in pixels, the api bounds it. */
export const versionPage1Url = (
  documentId: string,
  number: number,
  width?: number,
): string =>
  `${API_BASE}${versionPath(documentId, number)}/page1.png${
    width === undefined ? "" : `?width=${String(width)}`
  }`;

export const restoreVersion = (
  documentId: string,
  number: number,
): Promise<DocumentOut> =>
  sendJson(
    `${versionPath(documentId, number)}/restore`,
    "POST",
    documentSchema,
  );

export const approveVersion = (
  documentId: string,
  number: number,
): Promise<VersionSummary> =>
  sendJson(
    `${versionPath(documentId, number)}/approve`,
    "POST",
    versionSummarySchema,
  );

export const markSent = (
  documentId: string,
  number: number,
): Promise<VersionSummary> =>
  sendJson(
    `${versionPath(documentId, number)}/mark-sent`,
    "POST",
    versionSummarySchema,
  );

// Generations

export const getGeneration = (
  generationId: string,
  signal?: AbortSignal,
): Promise<GenerationDetail> =>
  getJson(
    `/document-generations/${pathId(generationId)}`,
    generationDetailSchema,
    signal,
  );

export const cancelGeneration = (generationId: string): Promise<Generation> =>
  sendJson(
    `/document-generations/${pathId(generationId)}/cancel`,
    "POST",
    generationSchema,
  );

// --- sentences ----------------------------------------------------------------------------------

/** What the person reads for a failed call: the api's own sentence when it sent one. */
export function failureSentence(error: unknown): string {
  return errorDetail(error) ?? "Something went wrong. Try again in a moment.";
}
