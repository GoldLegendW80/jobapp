/**
 * Apply to the selected postings (owner, 2026-11-06: "in the job list and map, i want being able
 * with the multi select to apply at all selected"; api/app/apply/bulk_router.py). The confirmation
 * reads what one click would start, then the click sends exactly the postings it showed, each as
 * it showed it.
 */
import { z } from "zod";

import { estimateSchema } from "@/features/documents/api/spend-api";
import { request } from "@/lib/api";

const postingSchema = z.object({
  posting_id: z.string().min(1),
  // Null for a posting that is no longer the person's.
  company: z.string().nullable(),
  title: z.string().nullable(),
});
export type BulkPosting = z.infer<typeof postingSchema>;

const itemSchema = postingSchema.extend({
  /** What the click writes first, in order: none, the letter, or the CV and the letter. */
  documents: z.array(z.string()),
  estimate: estimateSchema.nullable(),
  /** An application by e-mail: Review first, the person sends it. */
  email: z.boolean(),
  seen: z.string(),
});
export type BulkItem = z.infer<typeof itemSchema>;

const leftOutSchema = postingSchema.extend({
  code: z.string(),
  detail: z.string(),
});
export type BulkLeftOut = z.infer<typeof leftOutSchema>;

const previewSchema = z.object({
  review_before_send: z.boolean(),
  limit: z.number().int(),
  items: z.array(itemSchema),
  left_out: z.array(leftOutSchema),
  /** Past the limit: not read, not in this click. */
  beyond: z.array(postingSchema),
  estimate: estimateSchema.nullable(),
  cost_note: z.string(),
});
export type BulkPreview = z.infer<typeof previewSchema>;

const resultSchema = z.object({
  posting_id: z.string(),
  started: z.boolean(),
  application_id: z.string().nullable().optional(),
  mode: z.string().nullable().optional(),
  state: z.string().nullable().optional(),
  code: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
});
export type BulkResult = z.infer<typeof resultSchema>;

const startedSchema = z.object({
  results: z.array(resultSchema),
  started: z.number().int(),
  left_out: z.number().int(),
});
export type BulkStarted = z.infer<typeof startedSchema>;

const TIMEOUT_MS = 20_000;

/** What one click on "Apply to N" would do for these postings, in this order. */
export const previewBulkApply = (
  postingIds: readonly string[],
  signal?: AbortSignal,
): Promise<BulkPreview> =>
  request("/apply/bulk/preview", {
    method: "POST",
    body: { posting_ids: postingIds },
    signal,
    // Each posting's refusals and documents are read: a long selection takes longer.
    timeoutMs: TIMEOUT_MS + postingIds.length * 100,
    validate: (body) => previewSchema.parse(body),
  });

/**
 * The person's approval of exactly the postings the confirmation showed, each with what it
 * showed of it, and the review box it said. The api starts each through its own Apply click.
 */
export const startBulkApply = (shown: BulkPreview): Promise<BulkStarted> =>
  request("/apply/bulk", {
    method: "POST",
    body: {
      review_before_send: shown.review_before_send,
      items: shown.items.map((item) => ({
        posting_id: item.posting_id,
        seen: item.seen,
        documents: item.documents,
      })),
    },
    timeoutMs: TIMEOUT_MS + shown.items.length * 2_000,
    validate: (body) => startedSchema.parse(body),
  });
