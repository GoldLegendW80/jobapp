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

export const credentialSchema = z.object({
  kind: z.enum(credentialKinds),
  last4: z.string(),
  verified_at: z.string().nullable(),
  updated_at: z.string(),
});
export type ModelCredential = z.infer<typeof credentialSchema>;

export const verifySchema = z.object({
  result: z.enum(["verified", "unverified"]),
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
