/**
 * The permission of a job website searched in the person's browser (jobs J17, owner decision of
 * 2026-10-27): ticking the website in Find jobs and saving is the person's permission to search
 * it; unticking it and saving takes the permission back. The setup shows the server's current
 * risk sentence beside each box and records the decision through the platform's own consent
 * route, at the version of the sentence shown and the reviewed profile revision (LinkedIn:
 * PUT /api/browser/linkedin, browser B10; the others: PUT /api/opportunities/platforms/{kind},
 * opportunities O7b). The profile save never carries it, and the search still checks the stored
 * consent, the 7 day pause and the daily caps before every page (app/browser/platform_policy.py).
 */
import { z } from "zod";

import type { InteractiveSite } from "@/features/profile/api";
import { request } from "@/lib/api";

export const PROTECTED = [
  "linkedin",
  "welcome_to_the_jungle",
  "indeed",
  "freework",
] as const;
export type Protected = (typeof PROTECTED)[number];

/** LinkedIn and Free-Work hold their permission on a reader row, which needs search words. */
export const READERS: ReadonlySet<Protected> = new Set([
  "linkedin",
  "freework",
]);

// Host labels that name a platform, as the server reads an address (platform_policy.BRANDS).
const BRANDS = new Map<string, Protected>([
  ["linkedin", "linkedin"],
  ["lnkd", "linkedin"],
  ["indeed", "indeed"],
  ["free-work", "freework"],
  ["freework", "freework"],
  ["welcometothejungle", "welcome_to_the_jungle"],
  ["wttj", "welcome_to_the_jungle"],
]);

/**
 * The platform whose permission the search of a website needs, null for any other website. A
 * website of the person's own counts as the platform its host names, as on the server
 * (platform_policy.platform_of: any host label but the last), so `fr.indeed.com` or `indeed.fr`
 * shows Indeed's risk sentence. When this reads an address differently from the server, the
 * server's check still refuses the website without its permission.
 */
export function platformOf(
  site: Pick<InteractiveSite, "platform" | "url">,
): Protected | null {
  if (site.platform !== "custom") return site.platform;
  let host: string;
  try {
    host = new URL(site.url).hostname;
  } catch {
    return null;
  }
  const labels = host.toLowerCase().replace(/\.$/, "").split(".");
  for (const label of labels.length > 1 ? labels.slice(0, -1) : labels) {
    const platform = BRANDS.get(label);
    if (platform) return platform;
  }
  return null;
}

/** The platforms of the websites ticked in a list of websites. */
export function tickedOf(sites: readonly InteractiveSite[]): Set<Protected> {
  const found = new Set<Protected>();
  for (const site of sites) {
    const platform = site.enabled ? platformOf(site) : null;
    if (platform) found.add(platform);
  }
  return found;
}

export const permissionSchema = z.object({
  enabled: z.boolean(),
  accepted: z.boolean(),
  allowed: z.boolean(),
  text: z.string().min(1).max(20000),
  text_version: z.number().int().positive(),
  query: z.string().nullable().optional(),
  paused_until: z.string().nullable().optional(),
});
export type Permission = z.infer<typeof permissionSchema>;

export const permissionKey = (platform: Protected) =>
  ["search-permission", platform] as const;

const pathOf = (platform: Protected) =>
  platform === "linkedin"
    ? "/browser/linkedin"
    : `/opportunities/platforms/${platform}`;

export function getPermission(
  platform: Protected,
  signal?: AbortSignal,
): Promise<Permission> {
  return request(pathOf(platform), {
    signal,
    validate: (value) => permissionSchema.parse(value),
  });
}

export function putPermission(
  platform: Protected,
  body: Record<string, unknown>,
): Promise<Permission> {
  return request(pathOf(platform), {
    method: "PUT",
    body,
    validate: (value) => permissionSchema.parse(value),
  });
}

export interface PermissionInput {
  /** The platforms of the websites ticked in the draft being saved. */
  ticked: ReadonlySet<Protected>;
  /** The platforms ticked when the setup opened. */
  opened: ReadonlySet<Protected>;
  /** LinkedIn and Free-Work rows whose permission serves the websites' search only. */
  websitesOnly: ReadonlySet<Protected>;
  /** Each platform's state as just read from the server, missing when it could not be. */
  states: Partial<Record<Protected, Permission>>;
  /** The version of each platform's risk sentence shown beside its box. */
  shown: Partial<Record<Protected, number>>;
  /** The first title to include: the words of a new LinkedIn or Free-Work reader row. */
  words: string | null;
}

/** Whether saving takes back the permission of a platform whose box is unticked: Indeed's and
 * Welcome to the Jungle's always (they serve this search only), LinkedIn's and Free-Work's when
 * it was given for the websites or the box was ticked when the setup opened. A permission given
 * for an existing reader (and LinkedIn's Easy Apply) stays when its box was never ticked here. */
export function withdraws(
  platform: Protected,
  input: Omit<PermissionInput, "shown" | "words">,
): boolean {
  if (input.ticked.has(platform)) return false;
  const state = input.states[platform];
  if (!state) return input.opened.has(platform);
  return (
    state.enabled &&
    (!READERS.has(platform) ||
      input.websitesOnly.has(platform) ||
      input.opened.has(platform))
  );
}

export interface PermissionChange {
  platform: Protected;
  /** The body of the consent route, without the `base_revision` added when it is sent. */
  body: Record<string, unknown>;
}

export type PermissionPlan =
  | { changes: PermissionChange[] }
  | { refused: Protected; reason: "unloaded" | "unread" | "words" };

/**
 * The consent writes a Save makes: every take back first, then every grant, each in PROTECTED
 * order, so a write that fails part way leaves fewer permissions on, never more. A ticked
 * platform without a current permission is given one at the version of the sentence shown,
 * which must be the server's current one ("unread" otherwise: the person reads the new sentence
 * first); a platform whose state could not be read refuses the save ("unloaded"). Nothing is
 * written for a platform already in the state its box asks for.
 */
export function permissionPlan(input: PermissionInput): PermissionPlan {
  const withdrawals: PermissionChange[] = [];
  const grants: PermissionChange[] = [];
  for (const platform of PROTECTED) {
    const state = input.states[platform];
    if (input.ticked.has(platform)) {
      if (!state) return { refused: platform, reason: "unloaded" };
      if (state.enabled && state.accepted) continue;
      if (input.shown[platform] !== state.text_version)
        return { refused: platform, reason: "unread" };
      const body: Record<string, unknown> = {
        enabled: true,
        accept_version: state.text_version,
      };
      if (READERS.has(platform)) {
        // A new or switched off reader row is the websites' only: no reader starts with it.
        if (!state.enabled) body.interactive_only = true;
        if (!state.query) {
          if (!input.words) return { refused: platform, reason: "words" };
          body.query = input.words;
        }
      }
      grants.push({ platform, body });
    } else if (withdraws(platform, input)) {
      withdrawals.push({ platform, body: { enabled: false } });
    }
  }
  return { changes: [...withdrawals, ...grants] };
}

/** The words of a new reader row: the first title to include, as one line of at most 200
 * characters (the server's limit), or null when there is none. */
export function readerWords(titles: readonly string[]): string | null {
  for (const title of titles) {
    const words = title.split(/\s+/).filter(Boolean).join(" ").slice(0, 200);
    if (words.trim()) return words.trim();
  }
  return null;
}
