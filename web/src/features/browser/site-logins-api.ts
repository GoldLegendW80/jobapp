/**
 * Calls of /api/browser/sites (browser B17): the job websites the person signed in to in their
 * own browser, and the Done of a website's Sign in. No password goes through the app: the
 * person signs in on the site, in the live view, and the api only checks that the browser kept
 * a login of that website.
 */
import { z } from "zod";

import { request } from "@/lib/api";

const siteSchema = z.object({
  host: z.string(),
  state: z.enum(["signed_in", "expired"]),
  signed_in_at: z.string(),
});
const sitesSchema = z.object({ sites: z.array(siteSchema) });
const doneSchema = z.object({
  host: z.string(),
  state: z.enum(["signed_in", "not_signed_in"]),
  detail: z.string().nullable(),
});

export type SiteLogin = z.infer<typeof siteSchema>;
export type SiteDone = z.infer<typeof doneSchema>;

// Under ["browser"], so every change of the person's browser (Forget all logins) reads it again.
export const siteLoginsQueryKey = ["browser", "sites"] as const;

export function getSiteLogins(signal?: AbortSignal): Promise<SiteLogin[]> {
  return request("/browser/sites", {
    signal,
    validate: (data) => sitesSchema.parse(data).sites,
  });
}

/** Done: ends the sign in session and records the website's login when the browser kept one. */
export function siteDone(address: string): Promise<SiteDone> {
  return request("/browser/sites/done", {
    method: "POST",
    body: { address },
    timeoutMs: 40_000,
    validate: (data) => doneSchema.parse(data),
  });
}

/** The lower case host of a website's address, or null when it is not one. */
export function hostOf(address: string): string | null {
  try {
    const url = new URL(address);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.hostname.toLowerCase()
      : null;
  } catch {
    return null;
  }
}
