/**
 * The notes on an offer letter of one posting (SPEC-documents.md, "Reading an offer"): read
 * them, and read an uploaded letter into them. Shapes mirror api/app/documents/offer_router.py;
 * their chat is the documents chat (chat-api.ts).
 */
import { z } from "zod";

import { generationStatusSchema, languageSchema } from "./documents-api";
import { DOCUMENTS_ROOT, getJson, pathId, sendJson } from "./documents-request";

export const offerClauseSchema = z.object({
  title: z.string(),
  quote: z.string().default(""),
  plain: z.string(),
});
export type OfferClause = z.infer<typeof offerClauseSchema>;

export const offerNotesContentSchema = z.object({
  summary: z.string().default(""),
  clauses: z.array(offerClauseSchema).default([]),
  differences: z.array(z.string()).default([]),
  questions: z.array(z.string()).default([]),
});
export type OfferNotesContent = z.infer<typeof offerNotesContentSchema>;

export const offerNotesSchema = z.object({
  document_id: z.string().nullable(),
  language: languageSchema.nullable(),
  notes: offerNotesContentSchema.nullable(),
  /** The newest version's number: null before the first notes are stored. */
  version: z.number().nullable(),
  /** The newest reading, whatever its status. */
  reading: z
    .object({
      id: z.string(),
      status: generationStatusSchema,
      error: z.string().nullable(),
    })
    .nullable(),
  /** A reading or a chat change is open. */
  busy: z.boolean(),
  /** The file read; null once the person deleted it. */
  letter: z.object({ id: z.string(), name: z.string() }).nullable(),
  /** What the letter is compared with. */
  pay: z.object({
    posting: z.object({ pay: z.string().nullable() }),
    pay_sought: z.array(z.string()),
  }),
});
export type OfferNotes = z.infer<typeof offerNotesSchema>;

export const offerNotesKey = (jobId: string) =>
  ["documents", "offer-notes", jobId] as const;

const notesPath = (jobId: string) =>
  `${DOCUMENTS_ROOT}/offer-notes/${pathId(jobId)}`;

export const getOfferNotes = (
  jobId: string,
  signal?: AbortSignal,
): Promise<OfferNotes> => getJson(notesPath(jobId), offerNotesSchema, signal);

export const readOffer = (
  jobId: string,
  ingestionDocumentId: string,
): Promise<OfferNotes> =>
  sendJson(notesPath(jobId), "POST", offerNotesSchema, {
    ingestion_document_id: ingestionDocumentId,
  });
