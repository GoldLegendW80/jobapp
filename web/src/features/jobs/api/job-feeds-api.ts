/** The job feeds' state (GET /api/jobs/feeds) and Adzuna's key (PUT and DELETE). */
import { z } from "zod";

import { request } from "@/lib/api";

const feedSchema = z.object({
  name: z.string(),
  needs_key: z.boolean(),
  key_stored: z.boolean().default(false),
  key_last4: z.string().nullable().default(null),
  key_saved_at: z.string().nullable().default(null),
});
const feedsSchema = z.object({
  feeds: z.array(feedSchema),
  keys_available: z.boolean(),
});
export type FeedState = z.infer<typeof feedSchema>;
export type FeedsState = z.infer<typeof feedsSchema>;

export const jobFeedsKey = ["jobs", "feeds"] as const;

export function getJobFeeds(signal?: AbortSignal): Promise<FeedsState> {
  return request("/jobs/feeds", {
    signal,
    validate: (value) => feedsSchema.parse(value),
  });
}

export function saveAdzunaKey(body: {
  app_id: string;
  app_key: string;
}): Promise<FeedState> {
  return request("/jobs/feeds/adzuna/key", {
    method: "PUT",
    body,
    validate: (value) => feedSchema.parse(value),
  });
}

export async function removeAdzunaKey(): Promise<void> {
  await request("/jobs/feeds/adzuna/key", { method: "DELETE" });
}
