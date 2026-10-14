import { createHash, randomBytes } from "node:crypto";

import { expect, type Page } from "@playwright/test";

import { caddyUrl } from "./stack";

/**
 * Helpers of the mail journeys (task M1). A later task never edits this file: it adds helpers
 * of its own in web/e2e/mail-m<n>-helpers.ts.
 *
 * Everything here talks to the fake Google of tests/gmail-stub.compose.yml (the service
 * gmailstub, api/tests/mail/fake_gmail.py), never to Google. Its address on the host is
 * E2E_GMAIL_STUB_URL (http://127.0.0.1:<GMAIL_STUB_PORT>).
 *
 * - `stub`: the fake's control API. `stub.reset()` forgets grants, failures and sent mail and
 *   puts the three seed mailboxes back (alice, bob, bulk: api/tests/mail/fixtures/mailboxes);
 *   `stub.seed(...)` adds or replaces a mailbox; `stub.addMessage(...)` makes a mail arrive;
 *   `stub.fail(...)` makes the next calls of an endpoint fail; `stub.sent()` and
 *   `stub.calls()` read back what the app did.
 * - `consentUrl`, `allowOnConsentPage`, `denyOnConsentPage`: the fake's consent page, driven as
 *   a user would.
 * - `connectMailbox`, `syncNow`: written against the routes SPEC-mail.md plans
 *   (GET /api/mail/oauth/start, POST /api/mail/sync). Those routes do not exist yet: M4 and M10
 *   prove these two helpers in their own journeys.
 */

export const stubUrl = process.env.E2E_GMAIL_STUB_URL;

/** The mailboxes the fake starts with. Synthetic people, never a real account. */
export const ALICE = "e2e-alice@example.org";
export const BOB = "e2e-bob@example.org";
export const BULK = "e2e-bulk@example.org";

/** The OAuth client of the fake (tests/gmail-stub.compose.yml gives the same to the api). */
export const FAKE_CLIENT_ID = "fake-client-id.apps.fake-google.test";
export const FAKE_CLIENT_SECRET = "fake-client-secret-0123456789";

export const SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly";
export const SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send";
export const SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email";
export const SCOPES = ["openid", SCOPE_EMAIL, SCOPE_READONLY, SCOPE_SEND];

/** Where Google sends the browser back: the route M4 adds. */
export const redirectUri = `${caddyUrl}/api/mail/oauth/callback`;

function base(): string {
  if (!stubUrl) throw new Error("set E2E_GMAIL_STUB_URL to the fake Google");
  return stubUrl;
}

async function control<T>(name: string, body?: unknown): Promise<T> {
  const response = await fetch(`${base()}/__control/${name}`, {
    method: body === undefined ? "GET" : "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok)
    throw new Error(`fake Google, ${name}: ${response.status} ${text}`);
  return JSON.parse(text) as T;
}

export type StubCall = {
  n: number;
  endpoint: string;
  method: string;
  path: string;
  query: Record<string, string | null>;
  email: string | null;
  status: number | string;
};

export type StubSent = {
  email: string;
  id: string;
  threadId: string;
  messageId: string;
  messageIdGiven: string | null;
  headers: Record<string, string>;
  raw: string;
};

/** A message of a mailbox fixture: see `Mailbox` in api/tests/mail/fake_gmail.py. */
export type StubMessage = {
  id?: string;
  threadId?: string;
  labelIds?: string[];
  internalDate?: string | number;
  gmail?: Record<string, string>;
  headers: Array<[string, string]>;
  text?: string;
  html?: string;
  attachments?: Array<{
    filename: string;
    mimeType?: string;
    base64?: string;
    size?: number;
  }>;
};

export const stub = {
  health: async (): Promise<boolean> =>
    (await fetch(`${base()}/healthz`)).status === 200,
  reset: () => control<{ ok: true }>("reset", {}),
  /** A seed mailbox by name ("alice", "bob", "bulk"), or a whole fixture. */
  seed: (mailbox: string | { email: string; messages: StubMessage[] }) =>
    control<{ email: string; messages: number; historyId: string }>(
      "mailboxes",
      typeof mailbox === "string" ? { name: mailbox } : mailbox,
    ),
  /** A grant made without the browser. */
  grant: (email: string, scopes: string[] = SCOPES) =>
    control<{ refresh_token: string }>("grant", { email, scopes }),
  /** The user takes the access away at Google. */
  revoke: (email: string) => control<{ ok: true }>("revoke", { email }),
  expireAccess: (email?: string) =>
    control<{ ok: true }>("expire-access", email ? { email } : {}),
  /** The next `times` calls of `endpoint` fail: see `fail` kinds in fake_gmail.py. */
  fail: (endpoint: string, kind: string, times = 1, retryAfter?: number) =>
    control<{ ok: true }>("fail", {
      endpoint,
      kind,
      times,
      ...(retryAfter === undefined ? {} : { retry_after: retryAfter }),
    }),
  switch: (
    name: "send_message_id" | "send_answer" | "repeat_page_token",
    value: string,
  ) => control<{ ok: true }>("switch", { name, value }),
  clock: (now: string) => control<{ ok: true }>("clock", { now }),
  historyFloor: (email: string, historyId: string) =>
    control<{ ok: true }>("history-floor", { email, historyId }),
  /** A new mail arrives in the mailbox (one history record). */
  addMessage: (email: string, message: StubMessage) =>
    control<{ id: string; threadId: string; historyId: string }>("messages", {
      email,
      message,
    }),
  deleteMessage: (email: string, id: string) =>
    control<{ deleted: boolean }>("delete-message", { email, id }),
  labels: (email: string, id: string, add: string[], remove: string[] = []) =>
    control<{ changed: boolean }>("labels", { email, id, add, remove }),
  calls: async (endpoint?: string, email?: string): Promise<StubCall[]> => {
    const query = new URLSearchParams();
    if (endpoint) query.set("endpoint", endpoint);
    if (email) query.set("email", email);
    return (await control<{ calls: StubCall[] }>(`calls?${query}`)).calls;
  },
  sent: async (email?: string): Promise<StubSent[]> => {
    const query = new URLSearchParams();
    if (email) query.set("email", email);
    return (await control<{ sent: StubSent[] }>(`sent?${query}`)).sent;
  },
};

/** A PKCE pair as the app makes one: a random verifier and its S256 challenge. */
export function pkce(): { verifier: string; challenge: string } {
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  return { verifier, challenge };
}

/** The address of the fake's consent page for a request the app could have made. */
export function consentUrl(options: {
  state: string;
  challenge: string;
  scopes?: string[];
  loginHint?: string;
  redirect?: string;
  clientId?: string;
}): string {
  const query = new URLSearchParams({
    client_id: options.clientId ?? FAKE_CLIENT_ID,
    redirect_uri: options.redirect ?? redirectUri,
    response_type: "code",
    scope: (options.scopes ?? SCOPES).join(" "),
    state: options.state,
    code_challenge: options.challenge,
    code_challenge_method: "S256",
    access_type: "offline",
    prompt: "consent",
  });
  if (options.loginHint) query.set("login_hint", options.loginHint);
  return `${base()}/o/oauth2/v2/auth?${query}`;
}

/**
 * On the fake's consent page: type the account, untick the scopes in `untick` (Google lets a
 * user grant fewer scopes than asked), click Allow. Resolves once the browser has left the fake.
 */
export async function allowOnConsentPage(
  page: Page,
  account: string,
  untick: string[] = [],
) {
  await expect(
    page.getByRole("heading", { name: "Sign in with Google (fake)" }),
  ).toBeVisible();
  await page.getByLabel("Account").fill(account);
  for (const scope of untick)
    await page.getByRole("checkbox", { name: scope }).uncheck();
  await Promise.all([
    page.waitForURL((url) => url.origin !== new URL(base()).origin),
    page.getByRole("button", { name: "Allow" }).click(),
  ]);
}

export async function denyOnConsentPage(page: Page) {
  await expect(
    page.getByRole("heading", { name: "Sign in with Google (fake)" }),
  ).toBeVisible();
  await Promise.all([
    page.waitForURL((url) => url.origin !== new URL(base()).origin),
    page.getByRole("button", { name: "Deny" }).click(),
  ]);
}

/** The code for tokens at the fake, as the api will do it server side (M4). */
export async function exchangeCode(
  code: string,
  verifier: string,
  redirect = redirectUri,
): Promise<{
  status: number;
  body: Record<string, string | number | undefined>;
}> {
  const response = await fetch(`${base()}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      code_verifier: verifier,
      redirect_uri: redirect,
      client_id: FAKE_CLIENT_ID,
      client_secret: FAKE_CLIENT_SECRET,
    }),
  });
  return {
    status: response.status,
    body: (await response.json()) as Record<
      string,
      string | number | undefined
    >,
  };
}

/**
 * Connect the signed in user's Gmail through the fake: the app's own start route sends the
 * browser to the consent page, the user allows, Google sends it back to the app.
 * Written against GET /api/mail/oauth/start of SPEC-mail.md, which M4 builds.
 */
export async function connectMailbox(
  page: Page,
  account: string,
  untick: string[] = [],
) {
  await page.goto("/api/mail/oauth/start");
  await allowOnConsentPage(page, account, untick);
}

/** Ask for a sync now. Written against POST /api/mail/sync of SPEC-mail.md, which M10 builds. */
export async function syncNow(page: Page): Promise<number> {
  const response = await page.request.post("/api/mail/sync", {
    headers: { "X-Requested-With": "jobapp" },
  });
  return response.status();
}
