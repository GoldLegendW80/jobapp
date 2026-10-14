/**
 * Calls of /api/mail/account (SPEC-mail.md, Connection). Connecting is not a call: the browser
 * is sent to `START_PATH`, goes to Google, and comes back on /account?gmail=<word>.
 */
import { z } from "zod";

import { API_BASE, request } from "@/lib/api";

export const accountSchema = z.object({
  available: z.boolean(),
  status: z.enum([
    "not_connected",
    "ok",
    "needs_reconnect",
    "error",
    "disconnecting",
  ]),
  email: z.string().nullable(),
  can_read: z.boolean(),
  can_send: z.boolean(),
  connected_at: z.string().nullable(),
  last_sync_at: z.string().nullable(),
  consent: z.object({
    granted: z.boolean(),
    at: z.string().nullable(),
    version: z.number().nullable(),
    current_version: z.number(),
  }),
});
export type MailAccount = z.infer<typeof accountSchema>;

const disconnectSchema = z.object({
  disconnected: z.boolean(),
  revoked: z.boolean(),
});
export type Disconnected = z.infer<typeof disconnectSchema>;

/** The words the callback ends on: /account?gmail=<one of these>, and nothing else. */
export const outcomes = [
  "connected",
  "denied",
  "taken",
  "scope",
  "error",
] as const;
export type Outcome = (typeof outcomes)[number];

export function readOutcome(value: string | null): Outcome | undefined {
  return outcomes.find((known) => known === value);
}

export const mailAccountQueryKey = ["mail-account"] as const;
export const START_PATH = `${API_BASE}/mail/oauth/start`;
const PATH = "/mail/account";
// Disconnect waits for Google's answer, which the server retries for a few seconds.
const DISCONNECT_TIMEOUT_MS = 60_000;

export function getMailAccount(signal?: AbortSignal): Promise<MailAccount> {
  return request(PATH, {
    signal,
    validate: (data) => accountSchema.parse(data),
  });
}

export function saveConsent(body: {
  granted: boolean;
  version: number;
}): Promise<MailAccount> {
  return request(`${PATH}/consent`, {
    method: "PUT",
    body,
    validate: (data) => accountSchema.parse(data),
  });
}

export function disconnectMail(): Promise<Disconnected> {
  return request(PATH, {
    method: "DELETE",
    timeoutMs: DISCONNECT_TIMEOUT_MS,
    validate: (data) => disconnectSchema.parse(data),
  });
}

/** What the card says for each word. No value of the address is ever shown. */
export const TAKEN =
  "This Gmail address is already connected to another account";
export const OUTCOME_TEXT: Record<Outcome, string> = {
  connected: "Gmail is connected.",
  denied: "Nothing was connected. You can connect Gmail whenever you want to.",
  taken: `${TAKEN}. Disconnect it there first, or choose another address.`,
  scope:
    "Gmail was not connected, because reading mail was not allowed. Connect again and leave that permission ticked.",
  error: "Gmail could not be connected. Try again.",
};

/** Leaving the app for Google's consent screen. An object, so a test can watch it. */
export const navigation = {
  go(address: string) {
    window.location.assign(address);
  },
};
