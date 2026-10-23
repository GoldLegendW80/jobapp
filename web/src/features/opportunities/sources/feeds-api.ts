/**
 * GET /api/opportunities/feeds (api/app/opportunities/feeds_router.py, opportunities O7): the
 * catalogue of freelance and project sources. The same for everyone: the sources the sweep reads,
 * with the attribution each asks, and the platforms it does not read by itself, with their one
 * line reason and the date of the check. The person's own choice is `search.feeds` of the profile.
 */
import { z } from "zod";

import { request } from "@/lib/api";

const supportedSchema = z.object({
  kind: z.string(),
  name: z.string(),
  description: z.string(),
  attribution: z.string(),
  terms_url: z.string(),
  checked_on: z.string(),
});
export type SupportedFeed = z.infer<typeof supportedSchema>;

const notSupportedSchema = z.object({
  kind: z.string(),
  name: z.string(),
  reason: z.string(),
  terms_url: z.string(),
  checked_on: z.string(),
});
export type RestrictedPlatform = z.infer<typeof notSupportedSchema>;

export const catalogueSchema = z.object({
  supported: z.array(supportedSchema),
  not_supported: z.array(notSupportedSchema),
});
export type FeedCatalogue = z.infer<typeof catalogueSchema>;

export const feedCatalogueKey = ["opportunities", "feeds"] as const;

export const getFeedCatalogue = (
  signal?: AbortSignal,
): Promise<FeedCatalogue> =>
  request("/opportunities/feeds", {
    signal,
    timeoutMs: 15_000,
    validate: (body) => catalogueSchema.parse(body),
  });
