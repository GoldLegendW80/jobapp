import { execFileSync } from "node:child_process";

import { type BrowserContext, expect, type Page, test } from "@playwright/test";

import {
  ALICE,
  allowOnConsentPage,
  denyOnConsentPage,
  stub,
  stubUrl,
} from "./mail-helpers";
import { caddyUrl, cliLink, newContext, root, setPassword } from "./stack";

/**
 * Task M4 of the mail module against the real stack through Caddy plus the fake Google
 * (tests/gmail-stub.compose.yml), no mocks and never a real Google account: connect from the
 * Account page with the consent ticked, a second user who tries the same Google account,
 * disconnect with the revocation seen by the fake, and a calm message after Deny. Run with
 * the other mail journeys, as mail-m1.spec.ts says.
 */
test.describe.configure({ mode: "serial" });
test.skip(
  !caddyUrl || !stubUrl,
  "set E2E_CADDY_URL and E2E_GMAIL_STUB_URL with the stack running",
);

const project = process.env.E2E_COMPOSE_PROJECT;
const run = Date.now().toString(36);
const USER_A = `e2e-mail-a-${run}@example.org`;
const USER_B = `e2e-mail-b-${run}@example.org`;
const PASSWORD = `e2e only ${run} not a secret`;
const TAKEN = "This Gmail address is already connected to another account";
const READS = [
  "messages.list",
  "messages.get",
  "history.list",
  "threads.get",
  "attachments.get",
];

let contextA: BrowserContext;
let contextB: BrowserContext;
let pageA: Page;
let pageB: Page;
// What Google sent back in the address of the callback: it must reach no log.
const secrets: string[] = [];

function logs(...services: string[]): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project ?? "",
      "-f",
      "compose.yml",
      "logs",
      "--no-color",
      ...services,
    ],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
}

const card = (page: Page) =>
  page.locator("[aria-labelledby='gmail-card-title']");

async function account(page: Page, email: string, name: string) {
  await page.goto(cliLink(email));
  await setPassword(page, PASSWORD, name);
  await page.goto("/account");
  await expect(card(page).getByText("Not connected")).toBeVisible();
}

/** Open the dialog, tick the consent when asked, and leave for the fake's consent page. */
async function leaveForGoogle(page: Page, tick: boolean) {
  await card(page).getByRole("button", { name: "Connect Gmail" }).click();
  const dialog = page.getByRole("dialog", { name: "Connect Gmail" });
  await expect(dialog.getByText("What the app reads")).toBeVisible();
  const box = dialog.getByRole("checkbox", {
    name: "Let the assistant read my job mails",
  });
  if (tick) {
    // The keyboard is enough.
    await box.focus();
    await page.keyboard.press("Space");
    await expect(box).toBeChecked();
  }
  await dialog.getByRole("button", { name: "Continue to Google" }).click();
}

function keepCallbackSecrets(page: Page) {
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname !== "/api/mail/oauth/callback") return;
    for (const name of ["code", "state"]) {
      const value = url.searchParams.get(name);
      if (value) secrets.push(value, encodeURIComponent(value));
    }
  });
}

test.beforeAll(async ({ browser }) => {
  await stub.reset();
  contextA = await newContext(browser);
  contextB = await newContext(browser);
  pageA = await contextA.newPage();
  pageB = await contextB.newPage();
  keepCallbackSecrets(pageA);
  keepCallbackSecrets(pageB);
});

test.afterAll(async () => {
  await contextA.close();
  await contextB.close();
});

test("a user connects Gmail from Account with the consent ticked", async () => {
  await account(pageA, USER_A, "Mail A");
  await leaveForGoogle(pageA, true);
  await allowOnConsentPage(pageA, ALICE);

  await expect(pageA).toHaveURL(/\/account$/);
  await expect(card(pageA).getByText(ALICE)).toBeVisible();
  await expect(
    card(pageA).getByText("Connected", { exact: true }),
  ).toBeVisible();
  await expect(card(pageA).getByRole("status")).toHaveText(
    "Gmail is connected.",
  );
  // The consent ticked before leaving is recorded, and survives a reload.
  const box = card(pageA).getByRole("checkbox", {
    name: "Let the assistant read my job mails",
  });
  await expect(box).toBeChecked();
  await pageA.reload();
  await expect(card(pageA).getByText(ALICE)).toBeVisible();
  await expect(box).toBeChecked();

  // Consent first: connecting read no mail.
  for (const endpoint of READS)
    expect(await stub.calls(endpoint, ALICE), endpoint).toEqual([]);
  expect(await stub.calls("getProfile", ALICE)).toHaveLength(1);
});

test("the card holds at 375 px in the dark, with no sideways scroll", async () => {
  await pageA.setViewportSize({ width: 375, height: 800 });
  await pageA.emulateMedia({ colorScheme: "dark" });
  await expect(card(pageA).getByText(ALICE)).toBeVisible();
  await expect(
    card(pageA).getByRole("button", { name: "Disconnect" }),
  ).toBeVisible();
  const overflow = await pageA.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await pageA.setViewportSize({ width: 1280, height: 800 });
  await pageA.emulateMedia({ colorScheme: "light" });
});

test("a second user who tries the same Google account is told it is taken", async () => {
  await account(pageB, USER_B, "Mail B");
  await leaveForGoogle(pageB, false);
  await allowOnConsentPage(pageB, ALICE);

  await expect(pageB).toHaveURL(/\/account$/);
  await expect(card(pageB).getByRole("alert")).toContainText(TAKEN);
  await expect(card(pageB).getByText("Not connected")).toBeVisible();
  await expect(card(pageB).getByText(ALICE)).toHaveCount(0);
  // The first user's connection is untouched, and nothing was revoked for it.
  expect(await stub.calls("revoke")).toEqual([]);
  await pageA.reload();
  await expect(
    card(pageA).getByText("Connected", { exact: true }),
  ).toBeVisible();
});

test("the first user disconnects and the fake reports the revocation", async () => {
  await card(pageA).getByRole("button", { name: "Disconnect" }).click();
  const confirm = pageA.getByRole("alertdialog", {
    name: "Disconnect Gmail?",
  });
  await expect(confirm.getByText(/Deleted from the app/)).toBeVisible();
  await expect(confirm.getByText(/Kept: your postings/)).toBeVisible();
  await confirm.getByRole("button", { name: "Disconnect Gmail" }).click();

  await expect(card(pageA).getByText("Not connected")).toBeVisible();
  await expect(card(pageA).getByText(ALICE)).toHaveCount(0);
  const revoked = await stub.calls("revoke");
  expect(revoked.map((call) => call.status)).toEqual([200]);
});

test("Deny at Google shows a calm message", async () => {
  await leaveForGoogle(pageA, false);
  await denyOnConsentPage(pageA);

  await expect(pageA).toHaveURL(/\/account$/);
  await expect(card(pageA).getByRole("status")).toHaveText(
    "Nothing was connected. You can connect Gmail whenever you want to.",
  );
  await expect(card(pageA).getByRole("alert")).toHaveCount(0);
  await expect(card(pageA).getByText("Not connected")).toBeVisible();
});

test("the code and the state of the callbacks are in no log, Caddy's included", () => {
  expect(project, "E2E_COMPOSE_PROJECT names the stack to read").toBeTruthy();
  // Two allowed callbacks (a code and a state each) and one denied (a state).
  expect(secrets.length).toBeGreaterThanOrEqual(10);
  const caddy = logs("caddy");
  expect(caddy).toContain("/api/mail/oauth/callback[redacted]");
  const all = caddy + logs("api", "worker");
  for (const secret of secrets) expect(all.includes(secret)).toBe(false);
});
