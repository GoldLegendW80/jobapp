/** The hiring panel of a document (documents D29, `GET /api/documents/{id}/panel`). */
import { z } from "zod";

import { DOCUMENTS_ROOT, getJson, pathId } from "./documents-request";

const panelReviewSchema = z.object({
  role: z.string(),
  label: z.string(),
  verdict: z.string().nullable(),
  missing: z.string().nullable(),
  line: z.string().nullable(),
  why: z.string().nullable(),
});

const panelMistakeSchema = z.object({
  number: z.number(),
  quote: z.string(),
  why: z.string(),
  flagged_by: z.array(z.string()),
  fix: z.string(),
});

export const panelSchema = z.object({
  id: z.string(),
  status: z.enum(["running", "done", "failed"]),
  kind: z.enum(["cv", "letter", "base"]),
  reviewed_version: z.number(),
  rebuilt_version: z.number().nullable(),
  reviews: z.array(panelReviewSchema),
  mistakes: z.array(panelMistakeSchema),
  questions: z.array(
    z.object({
      question: z.string(),
      section: z.enum(["facts", "experience", "skills", "answers"]),
    }),
  ),
  screen_before: z.string().nullable(),
  screen_after: z.string().nullable(),
  readers_hire: z.number(),
  readers_answered: z.number(),
  strong: z.boolean(),
  closest: z
    .object({ role: z.string(), label: z.string(), why: z.string() })
    .nullable(),
  failed: z.array(z.string()),
  created_at: z.string(),
  finished_at: z.string().nullable(),
});
export type Panel = z.infer<typeof panelSchema>;

export const panelOutSchema = z.object({
  panel: panelSchema.nullable(),
  input_tokens: z.number(),
  output_tokens: z.number(),
});
export type PanelOut = z.infer<typeof panelOutSchema>;

export const panelKey = (id: string) =>
  ["documents", "document", id, "panel"] as const;

/** The latest hiring panel of a document, null when none ran. */
export const getPanel = (
  documentId: string,
  signal?: AbortSignal,
): Promise<PanelOut> =>
  getJson(
    `${DOCUMENTS_ROOT}/${pathId(documentId)}/panel`,
    panelOutSchema,
    signal,
  );
