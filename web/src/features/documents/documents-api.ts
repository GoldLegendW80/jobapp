/**
 * Every call of the documents module (SPEC-documents.md, "API"), one typed function per row of
 * the table, served yet or not. Later tasks (D12, D13, D16, D17, D18, D24, D25c, D26) call these
 * and add nothing here. Shapes mirror the response models of api/app/documents/router.py and
 * base_router.py; the routes of tasks not merged yet (the posting write of D10, the spend of D10,
 * messages of D17, the capture of D25b, mark sent of D14) are typed from the spec and from their
 * open pull requests, and a difference found later is a one line correction here.
 *
 * Page images and PDFs are fetched by the browser with the session cookie, so they are
 * addresses (`...Url`), not calls.
 */
import { z } from "zod";

import { API_BASE, errorDetail, isApiError, request } from "@/lib/api";

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

const kindSchema = z.enum(KINDS);
const languageSchema = z.enum(LANGUAGES);
const renderStateSchema = z.enum(RENDER_STATES);
const statusSchema = z.enum(GENERATION_STATUSES);
const waitSchema = z.enum(WAIT_REASONS);
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

export const generationBriefSchema = z.object({
  status: statusSchema,
  wait_reason: waitSchema.nullable(),
  /** Only on a failed one: a code of the api's fixed list. */
  error: z.string().nullable().optional(),
});
export type GenerationBrief = z.infer<typeof generationBriefSchema>;

export const postingDocumentSchema = documentSchema.extend({
  approved: z.number().nullable(),
  sent: z.array(z.number()),
  generation: generationBriefSchema.nullable(),
});
export type PostingDocument = z.infer<typeof postingDocumentSchema>;

export const postingDocumentsSchema = z.object({
  documents: z.array(postingDocumentSchema),
});
export type PostingDocuments = z.infer<typeof postingDocumentsSchema>;

export const generationSchema = z.object({
  id: z.string(),
  status: statusSchema,
  wait_reason: waitSchema.nullable(),
  /** A code of the api's fixed list. */
  error: z.string().nullable(),
});
export type Generation = z.infer<typeof generationSchema>;

export const baseCvSchema = z.object({
  id: z.string(),
  name: z.string(),
  source: z.enum(BASE_SOURCES),
  template_slug: z.string(),
  language: languageSchema,
  is_default: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
  last_version_at: z.string().nullable(),
  render_state: renderStateSchema,
  /** The open one, else the last one. */
  generation: generationSchema.nullable(),
});
export type BaseCv = z.infer<typeof baseCvSchema>;

export const baseCvsSchema = z.object({
  base_cvs: z.array(baseCvSchema),
  max: z.number(),
});
export type BaseCvs = z.infer<typeof baseCvsSchema>;

const builtSchema = z.object({ base_cv: baseCvSchema });

export interface BaseCvRequest {
  from: "profile" | "copy" | "upload";
  name?: string;
  copy_of?: string;
  ingestion_document_id?: string;
  language?: Language;
}

/** A decimal arrives as a string. */
const decimal = z.union([z.string(), z.number()]);

export const estimateSchema = z.object({
  tokens: z.object({ low: z.number(), high: z.number() }),
  /** An API key only. */
  cost: z
    .object({ currency: z.string(), low: decimal, high: decimal })
    .nullable(),
});
export type Estimate = z.infer<typeof estimateSchema>;

export const consentSchema = z.object({
  version: z.number(),
  text: z.string(),
  accepted: z.boolean(),
  accepted_at: z.string().nullable(),
  /** Null without a credential. */
  model: z.string().nullable(),
  min_fit: z.number(),
  posting: estimateSchema,
  week: estimateSchema.extend({
    postings: z.number(),
    postings_30_days: z.number(),
  }),
  cost_note: z.string(),
});
export type Consent = z.infer<typeof consentSchema>;

export const messageSchema = z.object({
  id: z.number(),
  channel: z.enum(["content", "design"]),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  generation_id: z.string().nullable(),
  version_number: z.number().nullable(),
  at: z.string(),
});
export type Message = z.infer<typeof messageSchema>;

export const messagesSchema = z.object({ messages: z.array(messageSchema) });

/** What sending a message answers: the message and the generation it started (202). */
export const sentMessageSchema = z.object({
  message: messageSchema,
  generation: generationSchema,
});
export type SentMessage = z.infer<typeof sentMessageSchema>;

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

/** "Write now": one generation per kind asked. */
export const writtenSchema = z.object({
  generations: z.array(
    z.object({
      id: z.string(),
      kind: kindSchema,
      status: statusSchema,
      wait_reason: waitSchema.nullable(),
    }),
  ),
});
export type Written = z.infer<typeof writtenSchema>;

export const queuedSchema = z.object({ render: renderSchema });
export type Queued = z.infer<typeof queuedSchema>;

export const templateSchema = z.object({
  slug: z.string(),
  version: z.number(),
  /** The person's own, captured from an uploaded CV. */
  user: z.boolean(),
  name: z.record(z.string(), z.string()),
  description: z.record(z.string(), z.string()),
  tags: z.array(z.string()),
  photo: z.string(),
  accent: z.string().nullable(),
  kinds: z.array(z.string()),
  /** Kind to address; empty for a person's own template. */
  thumbnails: z.record(z.string(), z.string()),
});
export type Template = z.infer<typeof templateSchema>;

export const templatesSchema = z.object({ templates: z.array(templateSchema) });

const tokensSpent = z.object({
  input: z.number(),
  output: z.number(),
  cost_usd: decimal,
});

export const spendSchema = z.object({
  day: z.string(),
  auto: z.boolean(),
  min_fit: z.number(),
  daily_ceiling: z.number(),
  written_today: z.number(),
  waiting: z.number(),
  postings_30_days: z.number(),
  to_write: z.number(),
  to_write_estimate: estimateSchema,
  /** Of `to_write`, those under the person's own threshold: what lowering it would add. Optional
   * because answers recorded before it (other tasks' test doubles) lack it; then nothing is shown. */
  newly: z.number().optional(),
  newly_estimate: estimateSchema.optional(),
  priced: z.boolean(),
  cost_note: z.string(),
  today: tokensSpent,
  last_30_days: tokensSpent,
});
export type Spend = z.infer<typeof spendSchema>;

export const settingsSchema = z.object({
  no_dash_separators: z.boolean(),
  banned_phrases: z.array(z.string()),
});
export type DocumentSettings = z.infer<typeof settingsSchema>;

/** The body of a 409 on a draft save: the current document, for the "changed in another tab" view. */
export const staleDraftSchema = z.object({
  detail: z.string(),
  document: documentSchema,
});
export type StaleDraft = z.infer<typeof staleDraftSchema>;

// --- keys ---------------------------------------------------------------------------------------

export const baseCvsKey = ["documents", "base-cvs"] as const;
export const consentKey = ["documents", "consent"] as const;
export const documentKey = (id: string) =>
  ["documents", "document", id] as const;
export const versionsKey = (id: string) =>
  ["documents", "document", id, "versions"] as const;
export const versionKey = (id: string, number: number) =>
  ["documents", "document", id, "version", number] as const;
export const messagesKey = (id: string, channel: Channel = "content") =>
  ["documents", "document", id, "messages", channel] as const;
export const postingDocumentsKey = (jobId: string) =>
  ["documents", "posting", jobId] as const;
export const generationKey = (id: string) =>
  ["documents", "generation", id] as const;
export const templatesKey = ["documents", "templates"] as const;
export const spendKey = (minFit?: number) =>
  ["documents", "spend", minFit ?? "current"] as const;
export const settingsKey = ["documents", "settings"] as const;

// --- calls --------------------------------------------------------------------------------------

const ROOT = "/documents";
const TIMEOUT_MS = 30_000;
/** The api path of a document or a posting: an id is encoded, never trusted into a path. */
const id = (value: string | number) => encodeURIComponent(String(value));

function get<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  return request(path, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => schema.parse(body),
  });
}

function send<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH",
  schema: z.ZodType<T>,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  return request(path, {
    method,
    body,
    headers,
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => schema.parse(answer),
  });
}

function remove(path: string): Promise<void> {
  return request(path, { method: "DELETE", timeoutMs: TIMEOUT_MS });
}

// Base CVs

export const listBaseCvs = (signal?: AbortSignal): Promise<BaseCvs> =>
  get(`${ROOT}/base-cvs`, baseCvsSchema, signal);

/** From the profile (202, the generation follows in the list) or a copy (201, made at once). */
export const createBaseCv = (body: BaseCvRequest): Promise<BaseCv> =>
  send(`${ROOT}/base-cvs`, "POST", builtSchema, body).then(
    (built) => built.base_cv,
  );

export const patchBaseCv = (
  baseId: string,
  change: { name?: string; default?: boolean },
): Promise<BaseCv> =>
  send(`${ROOT}/base-cvs/${id(baseId)}`, "PATCH", baseCvSchema, change);

export const deleteBaseCv = (baseId: string): Promise<void> =>
  remove(`${ROOT}/base-cvs/${id(baseId)}`);

// Design capture of an upload

export const captureDesign = (documentId: string): Promise<Generation> =>
  send(`${ROOT}/${id(documentId)}/design/capture`, "POST", generationSchema);

/** The original's page image (a PDF upload), rendered on demand; 404 for a DOCX. */
export const originalPageUrl = (documentId: string, page: number): string =>
  `${API_BASE}${ROOT}/${id(documentId)}/original?page=${String(page)}`;

// A posting's documents

export const getPostingDocuments = (
  jobId: string,
  signal?: AbortSignal,
): Promise<PostingDocuments> =>
  get(`/jobs/${id(jobId)}/documents`, postingDocumentsSchema, signal);

export const writeNow = (
  jobId: string,
  body: { kinds: Kind[]; base_document_id?: string },
): Promise<Written> =>
  send(`/jobs/${id(jobId)}/documents/write`, "POST", writtenSchema, body);

// One document

export const getDocument = (
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentOut> =>
  get(`${ROOT}/${id(documentId)}`, documentSchema, signal);

/** Save the draft; a stale `revision` answers 409 with the current document (`staleDraft`). */
export const saveDraft = (
  documentId: string,
  content: Record<string, unknown>,
  revision: number,
): Promise<DocumentOut> =>
  send(`${ROOT}/${id(documentId)}/draft`, "PUT", documentSchema, content, {
    "If-Match": String(revision),
  });

export const patchDocument = (
  documentId: string,
  change: { language?: Language; template_slug?: string },
): Promise<DocumentOut> =>
  send(`${ROOT}/${id(documentId)}`, "PATCH", documentSchema, change);

export const renderDocument = (documentId: string): Promise<Queued> =>
  send(`${ROOT}/${id(documentId)}/render`, "POST", queuedSchema);

/** "Make it fit": a `shorten` generation. */
export const fitDocument = (documentId: string): Promise<Generation> =>
  send(`${ROOT}/${id(documentId)}/fit`, "POST", generationSchema);

/** The current document inside a 409 of `saveDraft`, else undefined. */
export function staleDraft(error: unknown): StaleDraft | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const parsed = staleDraftSchema.safeParse(error.body);
  return parsed.success ? parsed.data : undefined;
}

// Versions

export const listVersions = (
  documentId: string,
  signal?: AbortSignal,
): Promise<VersionSummary[]> =>
  get(
    `${ROOT}/${id(documentId)}/versions`,
    z.array(versionSummarySchema),
    signal,
  );

export const getVersion = (
  documentId: string,
  number: number,
  signal?: AbortSignal,
): Promise<Version> =>
  get(
    `${ROOT}/${id(documentId)}/versions/${id(number)}`,
    versionSchema,
    signal,
  );

/** The PDF, served inline. */
export const versionPdfUrl = (documentId: string, number: number): string =>
  `${API_BASE}${ROOT}/${id(documentId)}/versions/${id(number)}/pdf`;

/** The preview image of page 1 (cards and phones); `width` in pixels, the api bounds it. */
export const versionPage1Url = (
  documentId: string,
  number: number,
  width?: number,
): string =>
  `${API_BASE}${ROOT}/${id(documentId)}/versions/${id(number)}/page1.png${
    width === undefined ? "" : `?width=${String(width)}`
  }`;

export const restoreVersion = (
  documentId: string,
  number: number,
): Promise<DocumentOut> =>
  send(
    `${ROOT}/${id(documentId)}/versions/${id(number)}/restore`,
    "POST",
    documentSchema,
  );

export const approveVersion = (
  documentId: string,
  number: number,
): Promise<VersionSummary> =>
  send(
    `${ROOT}/${id(documentId)}/versions/${id(number)}/approve`,
    "POST",
    versionSummarySchema,
  );

export const markSent = (
  documentId: string,
  number: number,
): Promise<VersionSummary> =>
  send(
    `${ROOT}/${id(documentId)}/versions/${id(number)}/mark-sent`,
    "POST",
    versionSummarySchema,
  );

// The chat of a document

export const listMessages = (
  documentId: string,
  channel: Channel = "content",
  signal?: AbortSignal,
): Promise<Message[]> =>
  get(
    `${ROOT}/${id(documentId)}/messages?channel=${channel}`,
    messagesSchema,
    signal,
  ).then((answer) => answer.messages);

export const sendMessage = (
  documentId: string,
  text: string,
  channel: Channel = "content",
): Promise<SentMessage> =>
  send(`${ROOT}/${id(documentId)}/messages`, "POST", sentMessageSchema, {
    text,
    channel,
  });

// Generations

export const getGeneration = (
  generationId: string,
  signal?: AbortSignal,
): Promise<GenerationDetail> =>
  get(
    `/document-generations/${id(generationId)}`,
    generationDetailSchema,
    signal,
  );

export const cancelGeneration = (generationId: string): Promise<Generation> =>
  send(
    `/document-generations/${id(generationId)}/cancel`,
    "POST",
    generationSchema,
  );

// Templates

export const listTemplates = (signal?: AbortSignal): Promise<Template[]> =>
  get("/document-templates", templatesSchema, signal).then(
    (answer) => answer.templates,
  );

/** A thumbnail; the api puts the template version in the address it gives (`Template.thumbnails`). */
export const templateThumbnailUrl = (slug: string, kind: Kind): string =>
  `${API_BASE}/document-templates/${id(slug)}/thumbnail?kind=${kind}`;

export const deleteTemplate = (slug: string): Promise<void> =>
  remove(`/document-templates/${id(slug)}`);

// Consent

export const getConsent = (signal?: AbortSignal): Promise<Consent> =>
  get(`${ROOT}/consent`, consentSchema, signal);

/** Records the consent to the text of `version`, the one the dialog showed. */
export const acceptConsent = (version: number): Promise<Consent> =>
  send(`${ROOT}/consent`, "POST", consentSchema, { version });

// Spend and settings

export const getSpend = (
  minFit?: number,
  signal?: AbortSignal,
): Promise<Spend> =>
  get(
    `${ROOT}/spend${minFit === undefined ? "" : `?min_fit=${String(minFit)}`}`,
    spendSchema,
    signal,
  );

export const getSettings = (signal?: AbortSignal): Promise<DocumentSettings> =>
  get(`${ROOT}/settings`, settingsSchema, signal);

export const putSettings = (
  value: DocumentSettings,
): Promise<DocumentSettings> =>
  send(`${ROOT}/settings`, "PUT", settingsSchema, value);

// --- sentences ----------------------------------------------------------------------------------

/** What the person reads for a failed call: the api's own sentence when it sent one. */
export function failureSentence(error: unknown): string {
  return errorDetail(error) ?? "Something went wrong. Try again in a moment.";
}
