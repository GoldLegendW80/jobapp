/**
 * Calls of the review queue and the Like (SPEC-apply.md, "API"; api/app/apply/queue_router.py).
 * Approve and the answers are A6's routes, in apply-api.ts: every decision of the queue goes
 * through the same gate as the job page's Apply section.
 */
import { z } from "zod";

import { request } from "@/lib/api";

import { applicationSchema } from "./apply-api";

const TIMEOUT_MS = 15_000;

const documentCheckSchema = z.object({
  number: z.number().int().nullable(),
  fit: z.string().nullable(),
  corrections: z.number().int(),
});
export type DocumentCheck = z.infer<typeof documentCheckSchema>;

const queueItemSchema = applicationSchema.extend({
  company: z.string(),
  title: z.string(),
  shot_id: z.string().nullable(),
  left: z.number().int(),
  required_left: z.number().int(),
  documents: z.record(z.string(), documentCheckSchema),
  documents_review: z.boolean(),
  reason: z
    .object({ code: z.string(), sentence: z.string().nullable() })
    .nullable(),
});
export type QueueItem = z.infer<typeof queueItemSchema>;

const queueSchema = z.object({
  items: z.array(queueItemSchema),
  waiting_on_you: z.number().int(),
});
export type Queue = z.infer<typeof queueSchema>;

const countSchema = z.object({ waiting_on_you: z.number().int() });

const rejectedSchema = z.object({
  application: applicationSchema,
  posting_status: z.string(),
});

const likedSchema = z.object({
  application: applicationSchema,
  documents: z.array(z.string()),
});
export type Liked = z.infer<typeof likedSchema>;

/** Invalidating this prefix reads the queue and the shell's count again. */
export const queueKey = ["apply", "queue"] as const;
export const queueListKey = [...queueKey, "list"] as const;
export const queueCountKey = [...queueKey, "count"] as const;

const id = encodeURIComponent;

export const getQueue = (signal?: AbortSignal): Promise<Queue> =>
  request("/apply/queue", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => queueSchema.parse(body),
  });

export const getQueueCount = async (signal?: AbortSignal): Promise<number> => {
  const count = await request("/apply/queue/count", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => countSchema.parse(body),
  });
  return count.waiting_on_you;
};

/** Reject the filled form; with `skipPosting` the posting also moves to Skipped. */
export const rejectQueued = (applicationId: string, skipPosting: boolean) =>
  request(`/apply/queue/${id(applicationId)}/reject`, {
    method: "POST",
    body: { skip_posting: skipPosting },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => rejectedSchema.parse(body),
  });

/** The Like: the application is created at once in Review first, or the api says why not (409). */
export const likePosting = (postingId: string): Promise<Liked> =>
  request(`/postings/${id(postingId)}/like`, {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => likedSchema.parse(body),
  });
