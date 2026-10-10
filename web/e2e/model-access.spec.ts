import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("agents-a1", "model-access");

/**
 * The Model access card of the account page (agents A1) against the real stack through Caddy:
 * a format error, a fake-format API key saved then refused by the check, a key the check
 * accepts, a subscription token stored as unverified, and removal with confirmation. The secret
 * never comes back to the page.
 *
 * The API key checks call GET /v1/models: with E2E_MODEL_STUB=1 the stack runs with
 * tests/anthropic-stub.compose.yml and the api asks that stub (keys containing e2egood pass,
 * others are refused); without it those two steps are skipped, so no run ever sends a key to
 * Anthropic. The subscription token makes no outbound call at all.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-a1 -f compose.yml -f compose.mac.yml -f tests/anthropic-stub.compose.yml \
 *     up -d --build --wait
 *   docker compose -p jobapp-a1 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-a1 E2E_MODEL_STUB=1 \
 *     pnpm --dir web exec playwright test model-access.spec.ts
 *   docker compose -p jobapp-a1 -f compose.yml -f tests/anthropic-stub.compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stub = Boolean(process.env.E2E_MODEL_STUB);
const run = Date.now().toString(36);
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
// Made up values of the right shape, fresh per run: never a real credential.
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
const BAD_KEY = `sk-ant-api03-e2ebad${tail()}${tail()}Bad1`;
const GOOD_KEY = `sk-ant-api03-e2egood${tail()}${tail()}Gd42`;
const TOKEN = `sk-ant-oat01-e2e${tail()}${tail()}Tk77`;

let context: BrowserContext;
let page: Page;
let errors: string[];

const card = () =>
  page.locator("[data-slot=card]").filter({
    has: page.getByRole("heading", { level: 2, name: "Model access" }),
  });
const form = () => page.getByRole("form", { name: "Model access" });
const savedBox = () => page.getByRole("region", { name: "Saved credential" });
const result = () => page.getByTestId("verify-result");

async function save(
  kind: "Anthropic API key" | "Claude subscription token",
  value: string,
) {
  await form().getByRole("radio", { name: kind }).check();
  await form()
    .getByLabel(
      kind === "Anthropic API key" ? "API key" : "Subscription token",
      { exact: true },
    )
    .fill(value);
  await form()
    .getByRole("button", { name: /^(Save|Replace) credential$/ })
    .click();
}

async function noSecretOnPage() {
  const html = await page.content();
  for (const secret of [BAD_KEY, GOOD_KEY, TOKEN])
    expect(html).not.toContain(secret.slice(0, 40));
}

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, [
    [404, /\/api\/me\/model-credential/],
    [422, /\/api\/me\/model-credential/],
    [400, /\/api\/me\/model-credential\/verify/],
  ]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([ADMIN]);
});

test("the account page shows an empty Model access card", async () => {
  const link = cliLink(ADMIN);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Admin" : undefined,
  );
  await page.goto("/account");
  await expect(card().getByTestId("no-credential")).toBeVisible();
  await expect(card()).toBeVisible();
  await expect(
    form().getByRole("radio", { name: "Anthropic API key" }),
  ).toBeChecked();
  await expect(form().getByLabel("API key", { exact: true })).toHaveAttribute(
    "type",
    "password",
  );
  await expect(
    form().getByRole("link", { name: "Anthropic Console" }),
  ).toHaveAttribute("href", "https://console.anthropic.com/settings/keys");
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  await shots(page, "empty");
});

test("a subscription token under the API key choice is refused, never echoed", async () => {
  await save("Anthropic API key", TOKEN);
  await expect(form().getByRole("alert")).toContainText(
    "This is a Claude subscription token.",
  );
  await expect(form().getByLabel("API key", { exact: true })).toBeFocused();
  await expect(card().getByTestId("no-credential")).toBeVisible();
  await shots(page, "format-error");
  await form().getByLabel("API key", { exact: true }).fill("");
});

test("a fake key is saved encrypted, only its last four characters come back", async () => {
  await save("Anthropic API key", BAD_KEY);
  await expect(savedBox().getByTestId("credential-last4")).toHaveText("Bad1");
  await expect(savedBox().getByTestId("credential-status")).toHaveText(
    "Not verified",
  );
  await expect(form().getByLabel("API key", { exact: true })).toHaveValue("");
  await noSecretOnPage();
  await page.reload();
  await expect(savedBox().getByTestId("credential-last4")).toHaveText("Bad1");
  await noSecretOnPage();
});

test("verify reports the fake key as refused", async () => {
  test.skip(
    !stub,
    "set E2E_MODEL_STUB=1 with tests/anthropic-stub.compose.yml",
  );
  await savedBox().getByRole("button", { name: "Verify" }).click();
  await expect(result()).toContainText("Anthropic refused this credential");
  await expect(savedBox().getByTestId("credential-status")).toHaveText(
    "Not verified",
  );
  await shots(page, "verify-refused");
});

test("a key the check accepts shows its verified date", async () => {
  test.skip(
    !stub,
    "set E2E_MODEL_STUB=1 with tests/anthropic-stub.compose.yml",
  );
  await save("Anthropic API key", GOOD_KEY);
  await expect(savedBox().getByTestId("credential-last4")).toHaveText("Gd42");
  await savedBox().getByRole("button", { name: "Verify" }).click();
  await expect(result()).toContainText("Anthropic accepted this key.");
  await expect(savedBox().getByTestId("credential-status")).toContainText(
    "Verified on",
  );
  await noSecretOnPage();
  await shots(page, "verified");
});

test("a subscription token is stored and left for the first run to check", async () => {
  await save("Claude subscription token", TOKEN);
  await expect(savedBox()).toContainText("Claude subscription token");
  await expect(savedBox().getByTestId("credential-last4")).toHaveText("Tk77");
  await savedBox().getByRole("button", { name: "Verify" }).click();
  await expect(result()).toContainText("checked by the first agent run");
  await expect(savedBox().getByTestId("credential-status")).toHaveText(
    "Not verified",
  );
  await noSecretOnPage();
  await shots(page, "subscription");
});

test("remove asks first, then the card is empty again", async () => {
  await savedBox().getByRole("button", { name: "Remove" }).click();
  const dialog = page.getByRole("alertdialog", {
    name: "Remove the credential?",
  });
  await expect(dialog).toBeVisible();
  await shots(page, "remove-confirm", false);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(savedBox()).toBeVisible();
  await savedBox().getByRole("button", { name: "Remove" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(card().getByTestId("no-credential")).toBeVisible();
  await page.reload();
  await expect(card().getByTestId("no-credential")).toBeVisible();
  expect(errors).toEqual([]);
});
