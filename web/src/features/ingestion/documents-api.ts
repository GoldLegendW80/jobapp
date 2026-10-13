/**
 * Calls of /api/ingestion/documents (SPEC-ingestion.md, api/app/ingestion/documents_router.py).
 * A file goes up as the raw request body with its name in `X-Filename` (RFC 5987); the server
 * decides its type from its bytes. Shapes mirror the router's response models.
 */
import { z } from "zod";

import {
  API_BASE,
  errorDetail,
  isApiError,
  request,
  upload,
  type UploadOptions,
} from "@/lib/api";

export const DOCUMENT_KINDS = [
  "text",
  "pdf",
  "archive",
  "image",
  // a type the api does not know: shown as a file, never read
  "other",
] as const;
export type DocumentKind = (typeof DOCUMENT_KINDS)[number];

export const DOCUMENT_STATUSES = [
  "pending",
  "awaiting_transcription",
  "done",
  "failed",
] as const;
export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];

export const documentSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.enum(DOCUMENT_KINDS),
  mime: z.string(),
  size: z.number(),
  status: z.enum(DOCUMENT_STATUSES),
  source: z.enum(["parsed", "transcribed", "pasted"]),
  /** false: this version has no reader for this kind of file, the document waits for one */
  readable: z.boolean(),
  note: z.string().nullable(),
  truncated: z.boolean(),
  pages: z.number().nullable(),
  pages_text: z.number().nullable(),
  pages_image: z.number().nullable(),
  failed_reason: z.string().nullable(),
  secret_warning: z.boolean(),
  secret_confirmed_at: z.string().nullable(),
  created_at: z.string(),
});
export type IngestionDocument = z.infer<typeof documentSchema>;

/** What adding a file or a text answers: the document, and whether it was already there. */
export const storedSchema = documentSchema.extend({ duplicate: z.boolean() });
export type StoredDocument = z.infer<typeof storedSchema>;

export const limitsSchema = z.object({
  file_bytes: z.number(),
  paste_characters: z.number(),
  text_characters: z.number(),
  user_bytes: z.number(),
  documents: z.number(),
});
export type Limits = z.infer<typeof limitsSchema>;

/** The limits as the api enforces them, used until the list has answered. */
export const DEFAULT_LIMITS: Limits = {
  file_bytes: 20 * 1024 * 1024,
  paste_characters: 100_000,
  text_characters: 500_000,
  user_bytes: 100 * 1024 * 1024,
  documents: 200,
};

export const documentListSchema = z.object({
  items: z.array(documentSchema),
  usage: z.object({ bytes: z.number(), documents: z.number() }),
  limits: limitsSchema,
});
export type DocumentList = z.infer<typeof documentListSchema>;

/** One stored page image: `position` is the zero based page of a PDF, 0 for an uploaded image. */
export const pageImageSchema = z.object({
  position: z.number().int().nonnegative(),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
});
export type PageImage = z.infer<typeof pageImageSchema>;

export const textPageSchema = z.object({
  text: z.string(),
  offset: z.number(),
  next_offset: z.number().nullable(),
  total: z.number(),
  // The pages that wait for the agent as images. Absent from an api older than these images.
  images: z.array(pageImageSchema).default([]),
});
export type TextPage = z.infer<typeof textPageSchema>;

export const documentsKey = ["ingestion", "documents"] as const;
export const documentTextKey = (id: string) =>
  ["ingestion", "documents", id, "text"] as const;

const BASE = "/ingestion/documents";
/** A slow link needs longer than the default for a list that follows a 20 MB upload. */
const TIMEOUT_MS = 30_000;
/**
 * A paste may wait its turn behind an upload of the same user, 35 s at most on the server
 * (SLOT_WAIT_S in documents_router.py): the page waits a little longer, so it shows the
 * server's answer, never a timeout of its own while the paste would still be taken.
 */
export const PASTE_TIMEOUT_MS = 45_000;
/** Characters of text asked for at a time by the preview. */
export const PREVIEW_PAGE = 20_000;
/** Longest name sent: the server keeps 200 characters. */
const NAME_MAX = 200;

export function listDocuments(signal?: AbortSignal): Promise<DocumentList> {
  return request(BASE, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => documentListSchema.parse(body),
  });
}

/**
 * The `X-Filename` value for a name: `UTF-8''` and the name percent encoded (RFC 5987), cut at
 * 200 characters. Undefined for a name that cannot be encoded (half a surrogate pair): the
 * server then calls the document "Untitled".
 */
export function filenameHeader(name: string): string | undefined {
  const cut = Array.from(name).slice(0, NAME_MAX).join("");
  try {
    const encoded = encodeURIComponent(cut).replace(
      /['()*!]/g,
      (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
    );
    return `UTF-8''${encoded}`;
  } catch {
    return undefined;
  }
}

export function uploadDocument(
  file: File,
  options: Pick<UploadOptions<StoredDocument>, "onProgress" | "signal"> = {},
): Promise<StoredDocument> {
  const name = filenameHeader(file.name);
  return upload(BASE, file, {
    ...options,
    // The server reads the type from the bytes; a declared type is never trusted.
    headers: {
      "Content-Type": "application/octet-stream",
      ...(name === undefined ? {} : { "X-Filename": name }),
    },
    validate: (body) => storedSchema.parse(body),
  });
}

export function pasteDocument(text: string): Promise<StoredDocument> {
  return request(`${BASE}/text`, {
    method: "POST",
    body: { text },
    timeoutMs: PASTE_TIMEOUT_MS,
    validate: (body) => storedSchema.parse(body),
  });
}

/**
 * Where the browser fetches one stored page image, with the session cookie: the api serves it
 * to its owner only, as a JPEG and nothing else.
 */
export function documentImageUrl(id: string, position: number): string {
  return `${API_BASE}${BASE}/${encodeURIComponent(id)}/images/${String(position)}`;
}

export function getDocumentText(
  id: string,
  offset: number,
  signal?: AbortSignal,
): Promise<TextPage> {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(PREVIEW_PAGE),
  });
  return request(`${BASE}/${encodeURIComponent(id)}/text?${query.toString()}`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => textPageSchema.parse(body),
  });
}

export function confirmDocument(id: string): Promise<IngestionDocument> {
  return request(`${BASE}/${encodeURIComponent(id)}/confirm`, {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => documentSchema.parse(body),
  });
}

/** Deletes for good. A document that is already gone counts as deleted. */
export async function deleteDocument(id: string): Promise<void> {
  try {
    await request(`${BASE}/${encodeURIComponent(id)}`, {
      method: "DELETE",
      timeoutMs: TIMEOUT_MS,
    });
  } catch (error) {
    if (isApiError(error) && error.status === 404) return;
    throw error;
  }
}

/** Characters as the server counts them: code points, an emoji is one. */
export function countCharacters(text: string): number {
  let count = 0;
  for (let i = 0; i < text.length; i += 1) {
    const unit = text.charCodeAt(i);
    // The second half of a surrogate pair belongs to the character before it.
    if (unit < 0xdc00 || unit > 0xdfff) count += 1;
  }
  return count;
}

/**
 * The statuses these routes answer with a sentence written for the user
 * (api/app/ingestion/documents_router.py). The body of any other answer (a 500, a 403, the
 * page of a proxy) was not written for the user and is never shown.
 */
const SPOKEN_STATUSES: ReadonlySet<number> = new Set([
  400, 404, 408, 409, 413, 415, 422, 429, 503,
]);
const SIGNED_OUT = "Your session has ended. Sign in again.";
const UNEXPECTED = "Something unexpected went wrong. Try again.";
const TOO_SLOW = "The server took too long to answer. Try again.";
const UNREADABLE =
  "The server sent an answer this page cannot read. Reload the page.";

/**
 * One sentence for a failed upload, paste, confirmation or deletion: the server's own when it
 * wrote one for the user (SPOKEN_STATUSES), else ours. `fileBytes` is the size limit to name
 * when a proxy refused the body before the api saw it.
 */
export function failureSentence(
  error: unknown,
  fileBytes: number = DEFAULT_LIMITS.file_bytes,
): string {
  if (!isApiError(error)) return UNEXPECTED;
  if (error.kind === "http") {
    const status = error.status ?? 0;
    const detail = SPOKEN_STATUSES.has(status) ? errorDetail(error) : undefined;
    if (detail) return detail;
    if (status === 413)
      return `The file is larger than ${megabytes(fileBytes)} MB.`;
    if (status === 401) return SIGNED_OUT;
    return "The server could not take it. Try again in a moment.";
  }
  if (error.kind === "timeout") return TOO_SLOW;
  if (error.kind === "parse") return UNREADABLE;
  return "It did not go through. Check your connection and try again.";
}

/**
 * One sentence for a list that could not be loaded. Nothing the server says about it is for
 * the user to act on, so its words and its status are not shown.
 */
export function loadFailureSentence(error: unknown): string {
  if (!isApiError(error)) return UNEXPECTED;
  switch (error.kind) {
    case "http":
      return error.status === 401
        ? SIGNED_OUT
        : "The server could not answer. Try again in a moment.";
    case "timeout":
      return TOO_SLOW;
    case "parse":
      return UNREADABLE;
    case "network":
    case "aborted":
      return "The server could not be reached. Check your connection and try again.";
  }
}

/** Whole mebibytes, shown as "MB" (20 MiB is "20 MB" everywhere in the app). */
export function megabytes(bytes: number): number {
  return Math.floor(bytes / (1024 * 1024));
}
