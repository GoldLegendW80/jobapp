import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import {
  ALICE,
  BOB,
  SCOPE_READONLY,
  SCOPE_SEND,
  allowOnConsentPage,
  consentUrl,
  denyOnConsentPage,
  exchangeCode,
  pkce,
  redirectUri,
  stub,
  stubUrl,
} from "./mail-helpers";
import { caddyUrl, makeShots, root } from "./stack";

/**
 * Task M1 of the mail module against the real stack through Caddy plus the fake Google
 * (tests/gmail-stub.compose.yml), no mocks and never a real Google account.
 *
 * The stack starts with `gmailstub` healthy; the browser opens the fake's consent page
 * directly, allows, and lands on the redirect address with a code. The app route behind that
 * address does not exist yet (M4 builds it), so the journey asserts the address only. This
 * proves the browser can reach the fake, which M4 needs. Then the other two legs M4 will lean
 * on: the code is good for tokens (PKCE verified), and the api container reaches the fake with
 * the app's own Google client.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   GMAIL_STUB_PORT=8098 HTTP_PORT=8097 docker compose -p jobapp-m1 -f compose.yml \
 *     -f tests/ats-stub.compose.yml -f tests/gmail-stub.compose.yml -f compose.mac.yml up -d --build --wait
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_GMAIL_STUB_URL=http://127.0.0.1:8098 \
 *     E2E_COMPOSE_PROJECT=jobapp-m1 pnpm --dir web exec playwright test -c e2e/mail-all.config.ts
 *   docker compose -p jobapp-m1 -f compose.yml -f tests/gmail-stub.compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(
  !caddyUrl || !stubUrl,
  "set E2E_CADDY_URL and E2E_GMAIL_STUB_URL with the stack running",
);

const project = process.env.E2E_COMPOSE_PROJECT;
const run = Date.now().toString(36);
const shots = makeShots("mail-m1", "mail-m1");

function compose(...args: string[]): string {
  return execFileSync(
    "docker",
    ["compose", "-p", project ?? "", "-f", "compose.yml", ...args],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
}

test.beforeAll(async () => {
  await stub.reset();
});

test("the stack starts with the fake Google healthy and seeded", async () => {
  expect(await stub.health()).toBe(true);
  test.skip(!project, "set E2E_COMPOSE_PROJECT to read the service's state");
  const state = execFileSync(
    "docker",
    [
      "ps",
      "--filter",
      `label=com.docker.compose.project=${project}`,
      "--filter",
      "label=com.docker.compose.service=gmailstub",
      "--format",
      "{{.Status}}",
    ],
    { encoding: "utf8" },
  );
  expect(state).toContain("(healthy)");
  // The three seed mailboxes are there, and a reset keeps them.
  for (const email of [ALICE, BOB]) {
    const grant = await stub.grant(email);
    expect(grant.refresh_token.length).toBeGreaterThan(40);
  }
});

test("the browser allows on the consent page and lands on the redirect address with a code", async ({
  page,
}) => {
  const state = `state-${run}`;
  const { verifier, challenge } = pkce();
  await page.goto(consentUrl({ state, challenge, loginHint: ALICE }));
  await expect(page.getByLabel("Account")).toHaveValue(ALICE);
  // One checkbox per scope asked, ticked, and both buttons.
  await expect(page.getByRole("checkbox")).toHaveCount(3);
  await expect(page.getByRole("checkbox", { name: SCOPE_SEND })).toBeChecked();
  await expect(page.getByRole("button", { name: "Deny" })).toBeVisible();
  await shots(page, "01-consent-page");

  await allowOnConsentPage(page, ALICE);
  const landed = new URL(page.url());
  expect(`${landed.origin}${landed.pathname}`).toBe(redirectUri);
  expect(landed.searchParams.get("state")).toBe(state);
  const code = landed.searchParams.get("code") ?? "";
  expect(code.length).toBeGreaterThan(30);
  expect(landed.searchParams.get("scope")?.split(" ")).toEqual(
    expect.arrayContaining([SCOPE_READONLY, SCOPE_SEND]),
  );
  expect(landed.searchParams.get("error")).toBeNull();

  // The code is good for tokens, once, and only with the verifier of this request.
  const wrong = await exchangeCode(code, pkce().verifier);
  expect(wrong.status).toBe(400);
  expect(wrong.body.error).toBe("invalid_grant");
  // A refused exchange spends the code, as at Google: a second consent gives a fresh one.
  await page.goto(consentUrl({ state, challenge, loginHint: ALICE }));
  await allowOnConsentPage(page, ALICE);
  const fresh = new URL(page.url()).searchParams.get("code") ?? "";
  expect(fresh).not.toBe(code);
  const tokens = await exchangeCode(fresh, verifier);
  expect(tokens.status).toBe(200);
  expect(String(tokens.body.refresh_token)).toMatch(/^1\/\//);
  expect(tokens.body.token_type).toBe("Bearer");
  expect((await exchangeCode(fresh, verifier)).status).toBe(400);
  const asked = (await stub.calls("token")).map(
    (call) => call.query.grant_type,
  );
  expect(asked).toEqual([
    "authorization_code",
    "authorization_code",
    "authorization_code",
  ]);
});

test("a user who unticks sending is granted the rest, and one who denies comes back with an error", async ({
  page,
}) => {
  const state = `state-narrow-${run}`;
  const { verifier, challenge } = pkce();
  await page.goto(consentUrl({ state, challenge }));
  await allowOnConsentPage(page, BOB, [SCOPE_SEND]);
  const landed = new URL(page.url());
  const granted = landed.searchParams.get("scope")?.split(" ") ?? [];
  expect(granted).toContain(SCOPE_READONLY);
  expect(granted).not.toContain(SCOPE_SEND);
  const tokens = await exchangeCode(
    landed.searchParams.get("code") ?? "",
    verifier,
  );
  expect(String(tokens.body.scope).split(" ")).not.toContain(SCOPE_SEND);

  await page.goto(consentUrl({ state, challenge }));
  await denyOnConsentPage(page);
  const denied = new URL(page.url());
  expect(`${denied.origin}${denied.pathname}`).toBe(redirectUri);
  expect(denied.searchParams.get("error")).toBe("access_denied");
  expect(denied.searchParams.get("state")).toBe(state);
  expect(denied.searchParams.get("code")).toBeNull();

  // What Google would refuse, the fake refuses: another client, another redirect address.
  const other = await page.goto(
    consentUrl({ state, challenge, clientId: "someone-else" }),
  );
  expect(other?.status()).toBe(401);
  const elsewhere = await page.goto(
    consentUrl({
      state,
      challenge,
      redirect: "http://127.0.0.1:1/somewhere/else",
    }),
  );
  expect(elsewhere?.status()).toBe(400);
  await expect(
    page.getByRole("heading", { name: /redirect_uri_mismatch/ }),
  ).toBeVisible();
});

test("the api container reaches the fake with the app's own Google client", async () => {
  test.skip(!project, "set E2E_COMPOSE_PROJECT to run a command in the api");
  const { refresh_token: refresh } = await stub.grant(ALICE);
  // The settings of the running api, its client, a real socket to gmailstub: what M4 and M7 use.
  const script = [
    "import asyncio, sys",
    "from app.config import get_settings",
    "from app.mail.google import GoogleClient, Grant",
    "client = GoogleClient(get_settings())",
    "grant = Grant('e2e-user', sys.stdin.read().strip())",
    "profile = asyncio.run(client.get_profile(grant))",
    "page = asyncio.run(client.list_messages(grant, q='from:ashbyhq.com'))",
    "print(client.endpoints.gmail_url, profile.email, [ref.id for ref in page.refs])",
  ].join("\n");
  const out = execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project ?? "",
      "-f",
      "compose.yml",
      "exec",
      "-T",
      "api",
      "python",
      "-c",
      script,
    ],
    { cwd: root, encoding: "utf8", input: refresh },
  );
  expect(out.trim()).toBe(
    `http://gmailstub:8098/gmail/v1/users/me ${ALICE} ['m-mistral-1']`,
  );
  const calls = await stub.calls(undefined, ALICE);
  expect(calls.map((call) => [call.endpoint, call.status])).toEqual(
    expect.arrayContaining([
      ["getProfile", 200],
      ["messages.list", 200],
    ]),
  );
  // Neither the api nor the worker wrote the token anywhere in its logs.
  const logs = compose("logs", "--no-color", "api", "worker");
  expect(logs).not.toContain(refresh);
});
