/**
 * The stored permission of a known job website, read and recorded through the platform's own
 * consent route (helpers/site-permissions.ts says what it means).
 */
import { z } from "zod";

import { request } from "@/lib/api";

import type { Protected } from "../helpers/site-permissions";

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
