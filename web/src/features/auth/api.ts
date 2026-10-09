/**
 * Calls of /api/auth (SPEC-identity.md). Bodies are checked with zod, so a proxy page or a
 * changed api is a `parse` error rather than a half filled user.
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

export const userSchema = z.object({
  id: z.string().min(1),
  email: z.string().min(1),
  name: z.string(),
  role: z.enum(["admin", "member"]),
  created_at: z.string(),
  last_login_at: z.string().nullable(),
});
export type User = z.infer<typeof userSchema>;

export const inviteSchema = z.object({
  email: z.string().min(1),
  kind: z.enum(["invite", "reset"]),
  expires_at: z.string(),
});
export type Invite = z.infer<typeof inviteSchema>;

const parseUser = (data: unknown): User => userSchema.parse(data);

/** The signed in user, or null when the browser has no valid session (401). */
export async function getMe(signal?: AbortSignal): Promise<User | null> {
  try {
    return await request("/auth/me", {
      signal,
      validate: parseUser,
      authRedirect: false,
    });
  } catch (error) {
    if (isApiError(error) && error.status === 401) return null;
    throw error;
  }
}

export function login(email: string, password: string): Promise<User> {
  return request("/auth/login", {
    method: "POST",
    body: { email, password },
    validate: parseUser,
    // A 401 here is "wrong email or password", shown on the form, not a lost session.
    authRedirect: false,
  });
}

export async function logout(): Promise<void> {
  await request("/auth/logout", { method: "POST", authRedirect: false });
}

/** Tokens travel in the path; encode them so a stray character can never change the route. */
const linkPath = (token: string) =>
  `/auth/invites/${encodeURIComponent(token)}`;

export function checkInvite(
  token: string,
  signal?: AbortSignal,
): Promise<Invite> {
  return request(linkPath(token), {
    signal,
    validate: (data) => inviteSchema.parse(data),
  });
}

export function acceptInvite(
  token: string,
  body: { name?: string; password: string },
): Promise<User> {
  return request(`${linkPath(token)}/accept`, {
    method: "POST",
    body,
    validate: parseUser,
    authRedirect: false,
  });
}

/**
 * Change the signed in user's password (204). The api ends every other session and keeps this
 * one. A wrong current password is a 400 naming `current_password`; a rule broken by the new
 * one is a 422 naming `new_password` ("<field>: <message>", see `fieldError`).
 */
export async function changePassword(body: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  await request("/auth/password", { method: "POST", body });
}

/** Rename the signed in user; returns the user as stored (trimmed, NFC). */
export function renameMe(name: string): Promise<User> {
  return request("/auth/me", {
    method: "PATCH",
    body: { name },
    validate: parseUser,
  });
}
