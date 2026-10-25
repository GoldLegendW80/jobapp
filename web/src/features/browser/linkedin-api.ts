import { z } from "zod";

import { request } from "@/lib/api";

const linkedinSchema = z.object({
  enabled: z.boolean(),
  accepted: z.boolean(),
  allowed: z.boolean(),
  text: z.string(),
  text_version: z.number(),
  accepted_at: z.string().nullable(),
  accepted_version: z.number().nullable(),
  query: z.string().nullable(),
  location: z.string().nullable(),
  paused_until: z.string().nullable(),
  last_sweep_at: z.string().nullable(),
  needs_signin: z.boolean(),
});
export type LinkedIn = z.infer<typeof linkedinSchema>;

type LinkedInIn = {
  enabled: boolean;
  accept_version?: number;
  query?: string;
  location?: string;
};

export const linkedinKey = ["browser", "linkedin"] as const;

export function getLinkedIn(signal?: AbortSignal): Promise<LinkedIn> {
  return request("/browser/linkedin", {
    signal,
    validate: (data) => linkedinSchema.parse(data),
  });
}

export function putLinkedIn(body: LinkedInIn): Promise<LinkedIn> {
  return request("/browser/linkedin", {
    method: "PUT",
    body,
    validate: (data) => linkedinSchema.parse(data),
  });
}
