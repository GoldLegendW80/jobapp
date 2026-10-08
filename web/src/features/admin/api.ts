/**
 * Calls of /api/admin/invites (SPEC-identity.md). Bodies are checked with zod. The link of a
 * new invite exists only in the create answer: callers show it once and never store it.
 */
import { z } from "zod";

import { request } from "@/lib/api";

export const inviteStatusSchema = z.enum([
  "active",
  "used",
  "revoked",
  "expired",
]);
export type InviteStatus = z.infer<typeof inviteStatusSchema>;

export const adminInviteSchema = z.object({
  id: z.string().min(1),
  email: z.string().min(1),
  kind: z.enum(["invite", "reset"]),
  role: z.enum(["admin", "member"]).nullable(),
  status: inviteStatusSchema,
  created_by_name: z.string().nullable(),
  created_at: z.string(),
  expires_at: z.string(),
  used_at: z.string().nullable(),
  revoked_at: z.string().nullable(),
});
export type AdminInvite = z.infer<typeof adminInviteSchema>;

export const invitePageSchema = z.object({
  items: z.array(adminInviteSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});
export type InvitePage = z.infer<typeof invitePageSchema>;

export const inviteCreatedSchema = z.object({
  invite: adminInviteSchema,
  path: z.string().regex(/^\/(invite|reset)\/[A-Za-z0-9_-]{43}$/),
});
export type InviteCreated = z.infer<typeof inviteCreatedSchema>;

export type NewInvite =
  | { email: string; kind: "invite"; role: "member" | "admin" }
  | { email: string; kind: "reset" };

/** How long a link lives, as the api sets it (SPEC-identity.md). */
export const LIFETIME = { invite: "7 days", reset: "1 hour" } as const;

export const INVITES_PAGE_SIZE = 20;
export const invitesKey = ["admin", "invites"] as const;

export function listInvites(
  offset: number,
  signal?: AbortSignal,
): Promise<InvitePage> {
  return request(`/admin/invites?limit=${INVITES_PAGE_SIZE}&offset=${offset}`, {
    signal,
    validate: (data) => invitePageSchema.parse(data),
  });
}

export function createInvite(body: NewInvite): Promise<InviteCreated> {
  return request("/admin/invites", {
    method: "POST",
    body,
    validate: (data) => inviteCreatedSchema.parse(data),
  });
}

export async function revokeInvite(id: string): Promise<void> {
  await request(`/admin/invites/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** The full link to share: the origin this admin is on, plus the api's path. */
export function fullLink(path: string, origin = window.location.origin) {
  return `${origin}${path}`;
}
