/** The CV and the letter written for one posting, and "Write now". */
import { z } from "zod";

import {
  documentSchema,
  generationStatusSchema,
  kindSchema,
  waitReasonSchema,
  type Kind,
} from "./documents-api";
import { getJson, pathId, sendJson } from "./documents-request";

export const generationBriefSchema = z.object({
  status: generationStatusSchema,
  wait_reason: waitReasonSchema.nullable(),
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

/** "Write now": one generation per kind asked. */
export const writtenSchema = z.object({
  generations: z.array(
    z.object({
      id: z.string(),
      kind: kindSchema,
      status: generationStatusSchema,
      wait_reason: waitReasonSchema.nullable(),
    }),
  ),
});
export type Written = z.infer<typeof writtenSchema>;

export const postingDocumentsKey = (jobId: string) =>
  ["documents", "posting", jobId] as const;

export const getPostingDocuments = (
  jobId: string,
  signal?: AbortSignal,
): Promise<PostingDocuments> =>
  getJson(`/jobs/${pathId(jobId)}/documents`, postingDocumentsSchema, signal);

export const writeNow = (
  jobId: string,
  body: { kinds: Kind[]; base_document_id?: string },
): Promise<Written> =>
  sendJson(
    `/jobs/${pathId(jobId)}/documents/write`,
    "POST",
    writtenSchema,
    body,
  );
