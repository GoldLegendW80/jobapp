/**
 * Calls of /api/me/model-credential (SPEC-agents.md, Credentials). The secret only ever goes
 * out (PUT); answers carry the kind, the last four characters and the dates.
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

export const credentialKinds = [
  "claude_subscription",
  "anthropic_api_key",
] as const;
export type CredentialKind = (typeof credentialKinds)[number];

/**
 * The worker's check of a subscription token (one Claude Code run, owner 2026-11-07), for the
 * credential as it is now; null for an API key, whose check answers at once, or before any.
 */
export const checkSchema = z.object({
  state: z.enum(["checking", "verified", "invalid", "busy", "unavailable"]),
  message: z.string(),
});
export type CredentialCheck = z.infer<typeof checkSchema>;

export const credentialSchema = z.object({
  kind: z.enum(credentialKinds),
  last4: z.string(),
  verified_at: z.string().nullable(),
  updated_at: z.string(),
  check: checkSchema.nullable().default(null),
});
// The input shape: `check` may be left out (null), as a server from before the check answers.
export type ModelCredential = z.input<typeof credentialSchema>;

export const verifySchema = z.object({
  // checking: a subscription token, checked by the worker; its outcome comes in `check`.
  // unverified: the answer of a server from before that check.
  result: z.enum(["verified", "checking", "unverified"]),
  message: z.string(),
  credential: credentialSchema,
});
export type VerifyResult = z.infer<typeof verifySchema>;

export const modelCredentialQueryKey = ["model-credential"] as const;
const PATH = "/me/model-credential";
// The server waits up to 10 seconds for Anthropic.
const VERIFY_TIMEOUT_MS = 20_000;

/** The saved credential, or null when there is none (404). */
export async function getModelCredential(
  signal?: AbortSignal,
): Promise<ModelCredential | null> {
  try {
    return await request(PATH, {
      signal,
      validate: (data) => credentialSchema.parse(data),
    });
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

export function saveModelCredential(body: {
  kind: CredentialKind;
  secret: string;
}): Promise<ModelCredential> {
  return request(PATH, {
    method: "PUT",
    body,
    validate: (data) => credentialSchema.parse(data),
  });
}

export async function removeModelCredential(): Promise<void> {
  await request(PATH, { method: "DELETE" });
}

export function verifyModelCredential(): Promise<VerifyResult> {
  return request(`${PATH}/verify`, {
    method: "POST",
    timeoutMs: VERIFY_TIMEOUT_MS,
    validate: (data) => verifySchema.parse(data),
  });
}
