import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type ConsoleMessage,
  type Locator,
  type Page,
} from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const evidence = path.join(
  root,
  "tasks/evidence",
  process.env.E2E_EVIDENCE ?? "identity-i4",
);
const prefix = process.env.E2E_SHOT_PREFIX ?? "";

/**
 * Invites from the admin area, against the real stack through Caddy: no mocks.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8094 docker compose -p jobapp-i4 -f compose.yml up -d --build --wait
 *   docker compose -p jobapp-i4 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8094 E2E_COMPOSE_PROJECT=jobapp-i4 \
 *     pnpm --dir web exec playwright test invites.spec.ts
 *   docker compose -p jobapp-i4 -f compose.yml down -v
 *
 * On the Jetson, through the public URL, with throwaway accounts removed afterwards (README):
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_FRIEND_EMAIL=e2e-friend@example.org E2E_SHOT_PREFIX=jetson- \
 *     pnpm --dir web exec playwright test invites.spec.ts
 */
const caddyUrl = process.env.E2E_CADDY_URL;
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const FRIEND = process.env.E2E_FRIEND_EMAIL ?? `friend-${run}@example.org`;
const ADMIN_NAME = "E2E Admin";
const FRIEND_NAME = "E2E Friend";
// Fresh per run, never a common password, never logged.
const ADMIN_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const FRIEND_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const FRIEND_NEW_PASSWORD = `Pw2 ${randomBytes(12).toString("base64url")}`;
const LINK = /\/(invite|reset)\/[A-Za-z0-9_-]{43}/;

/** A one-time link from the operator CLI, as a path to open on the base URL. */
function cliLink(email: string): string {
  const output = process.env.E2E_ADMIN_VIA_DEPLOY
    ? execFileSync(path.join(root, "deploy.sh"), ["admin", "create", email], {
        cwd: root,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "inherit"],
      })
    : execFileSync(
        "docker",
        [
          "compose",
          "-p",
          process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
          "-f",
          "compose.yml",
          "exec",
          "-T",
          "api",
          "python",
          "-m",
          "app.identity.cli",
          "create-admin",
          email,
          "--base-url",
          caddyUrl ?? "",
        ],
        { cwd: root, encoding: "utf8" },
      );
  const match = LINK.exec(/https?:\/\/\S+/.exec(output)?.[0] ?? "");
  if (!match) throw new Error("the CLI printed no link");
  return match[0];
}

/** Console errors, except the browser's own lines for answers the journey expects. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() !== "error") return;
    const text = `${msg.text()} @ ${msg.location().url}`;
    if (/status of 401/.test(text) && /\/api\/auth\/(me|login)/.test(text))
      return;
    if (/status of 404/.test(text) && /\/api\/auth\/invites\//.test(text))
      return;
    if (/status of 409/.test(text) && /\/api\/admin\/invites/.test(text))
      return;
    errors.push(text);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
] as const;
const SCHEMES = ["light", "dark"] as const;

/** One screenshot per viewport and colour scheme, then back to desktop light. */
async function shots(page: Page, name: string, fullPage = true) {
  fs.mkdirSync(evidence, { recursive: true });
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    for (const scheme of SCHEMES) {
      await page.emulateMedia({ colorScheme: scheme });
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      expect(overflow, `${name} ${viewport.name} ${scheme}`).toBe(0);
      await page.screenshot({
        path: path.join(
          evidence,
          `${prefix}${name}-${viewport.name}-${scheme}.png`,
        ),
        fullPage,
        animations: "disabled",
      });
    }
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light" });
}

async function setPassword(page: Page, password: string, name?: string) {
  if (name !== undefined) await page.getByLabel("Name").fill(name);
  await page.getByLabel(/^(New password|Password)$/).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page
    .getByRole("button", { name: /Create account|Save password/ })
    .click();
  await expect(page).toHaveURL(/\/$/);
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

const menuButton = (page: Page, name: string) =>
  page.getByRole("button", { name: `Account menu, ${name}` });

async function signOut(page: Page, name: string) {
  await menuButton(page, name).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

async function newContext(browser: Browser) {
  const context = await browser.newContext({ baseURL: caddyUrl });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: caddyUrl,
  });
  return context;
}

/** The newest row for an email (the list is newest first). */
const rowFor = (page: Page, email: string): Locator =>
  page
    .getByRole("table", { name: "Invites, newest first" })
    .getByRole("row")
    .filter({ hasText: email })
    .first();

/** Create a link from the dialog; returns the full link as shown. */
async function createLink(
  page: Page,
  email: string,
  kind: "invite" | "reset",
  shot?: string,
): Promise<string> {
  await page.getByRole("button", { name: "Invite someone" }).click();
  const dialog = page.getByRole("dialog", { name: "Invite someone" });
  await dialog.getByLabel("Email").fill(email);
  await dialog.getByLabel("Link").selectOption(kind);
  if (kind === "invite") await dialog.getByLabel("Role").selectOption("member");
  else await expect(dialog.getByLabel("Role")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Create link" }).click();
  const result = page.getByRole("dialog", {
    name: kind === "invite" ? "Invite link ready" : "Reset link ready",
  });
  await expect(result).toBeVisible();
  await expect(result).toContainText("shown only once");
  await expect(result).toContainText(
    kind === "invite" ? "expires in 7 days" : "expires in 1 hour",
  );
  const field = result.getByLabel(
    kind === "invite" ? "Invite link" : "Reset link",
  );
  const link = await field.inputValue();
  // Built from the origin the admin is on, whatever host that is.
  const origin = await page.evaluate(() => window.location.origin);
  expect(link.startsWith(`${origin}/${kind}/`)).toBe(true);
  expect(LINK.test(new URL(link).pathname)).toBe(true);

  await result.getByRole("button", { name: "Copy link" }).click();
  await expect(result.getByRole("button", { name: "Copied" })).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(link);
  if (shot) await shots(page, shot, false);
  await result.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Shown once: nowhere on the page any more.
  await expect(page.locator("body")).not.toContainText(new URL(link).pathname);
  return link;
}

let adminContext: BrowserContext;
let admin: Page;
let adminErrors: string[];
let friendContext: BrowserContext;
let friend: Page;
let revokedLink = "";

test.beforeAll(async ({ browser }) => {
  adminContext = await newContext(browser);
  admin = await adminContext.newPage();
  adminErrors = watchErrors(admin);
  friendContext = await newContext(browser);
  friend = await friendContext.newPage();
});

test.afterAll(async () => {
  await adminContext.close();
  await friendContext.close();
});

test("the admin from the CLI signs in and opens the admin area", async () => {
  const link = cliLink(ADMIN);
  await admin.goto(link);
  // A first run sets up the account; a rerun for an existing admin gets a reset link.
  await setPassword(
    admin,
    ADMIN_PASSWORD,
    link.startsWith("/invite/") ? ADMIN_NAME : undefined,
  );
  const name = link.startsWith("/invite/") ? ADMIN_NAME : null;
  if (name) await expect(menuButton(admin, name)).toBeVisible();
  await admin
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Admin" })
    .click();
  await expect(
    admin.getByRole("heading", { name: "Users", level: 1 }),
  ).toBeVisible();
  await expect(
    admin.getByRole("heading", { name: "Invites", level: 2 }),
  ).toBeVisible();
  // The CLI setup link is listed, used, with no creator.
  await expect(rowFor(admin, ADMIN)).toContainText("Used");
  expect(adminErrors).toEqual([]);
});

test("a revoked invite link shows the invalid page", async () => {
  revokedLink = await createLink(admin, FRIEND, "invite");
  const row = rowFor(admin, FRIEND);
  await expect(row).toContainText("Active");
  await expect(row).toContainText("in 7 days");
  await shots(admin, "admin-invites-active");

  // Cancel first: nothing happens.
  await row
    .getByRole("button", { name: `Revoke the link for ${FRIEND}` })
    .click();
  const confirm = admin.getByRole("alertdialog", { name: "Revoke this link?" });
  await expect(confirm).toContainText(FRIEND);
  await shots(admin, "admin-revoke-confirm", false);
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  await expect(row).toContainText("Active");

  await row
    .getByRole("button", { name: `Revoke the link for ${FRIEND}` })
    .click();
  await confirm.getByRole("button", { name: "Revoke link" }).click();
  await expect(
    admin.getByText("Link revoked. It no longer works."),
  ).toBeVisible();
  await expect(row).toContainText("Revoked");
  await expect(row.getByRole("button")).toHaveCount(0);

  await friend.goto(revokedLink);
  await expect(
    friend.getByRole("heading", { name: "This link is invalid or expired" }),
  ).toBeVisible();
  expect(adminErrors).toEqual([]);
});

test("the friend opens a copied invite in another browser and lands signed in as a member", async () => {
  const link = await createLink(admin, FRIEND, "invite", "admin-invite-link");
  await friend.goto(link);
  await expect(
    friend.getByRole("heading", { name: "Set up your account" }),
  ).toBeVisible();
  expect((await friend.getByLabel("Email").inputValue()).toLowerCase()).toBe(
    FRIEND.toLowerCase(),
  );
  await setPassword(friend, FRIEND_PASSWORD, FRIEND_NAME);
  await expect(menuButton(friend, FRIEND_NAME)).toBeVisible();
  // A member has no admin entry, and the admin page is the not found page.
  await expect(
    friend.getByRole("navigation", { name: "Main" }).getByRole("link", {
      name: "Admin",
    }),
  ).toHaveCount(0);
  await friend.goto("/admin/users");
  await expect(
    friend.getByRole("heading", { name: "Page not found" }),
  ).toBeVisible();
  await expect(friend.getByText("Invites")).toHaveCount(0);
  const api = await friend.evaluate(async () => {
    const response = await fetch("/api/admin/invites");
    return response.status;
  });
  expect(api).toBe(404);
  await shots(friend, "member-admin-404");

  // The used link is dead now.
  await friend.goto(link);
  await expect(
    friend.getByRole("heading", { name: "This link is invalid or expired" }),
  ).toBeVisible();
  await shots(friend, "invalid-link");
  await friend.goto("/");
});

test("the admin sees the invite as used, and cannot invite an existing account", async () => {
  await admin.reload();
  const row = rowFor(admin, FRIEND);
  await expect(row).toContainText("Used");
  await expect(row.getByRole("button")).toHaveCount(0);
  await shots(admin, "admin-invites-used");

  await admin.getByRole("button", { name: "Invite someone" }).click();
  const dialog = admin.getByRole("dialog", { name: "Invite someone" });
  await dialog.getByLabel("Email").fill(FRIEND.toUpperCase());
  await dialog.getByRole("button", { name: "Create link" }).click();
  await expect(
    dialog.getByText(
      "An account with this email already exists. Send a reset link instead.",
    ),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Create link" }).focus();
  await shots(admin, "admin-invite-dialog-error", false);
  await admin.keyboard.press("Escape");
  await expect(admin.getByRole("dialog")).toHaveCount(0);
  expect(adminErrors).toEqual([]);
});

test("a reset link from the admin area sets the friend's new password", async () => {
  const link = await createLink(admin, FRIEND, "reset");
  const row = rowFor(admin, FRIEND);
  await expect(row).toContainText("Password reset");
  await expect(row).toContainText("Active");

  // The friend is still signed in; the reset ends that session.
  await friend.goto(link);
  await expect(
    friend.getByRole("heading", { name: "Choose a new password" }),
  ).toBeVisible();
  await expect(friend.getByLabel("Name")).toHaveCount(0);
  await setPassword(friend, FRIEND_NEW_PASSWORD);
  await expect(menuButton(friend, FRIEND_NAME)).toBeVisible();
  await signOut(friend, FRIEND_NAME);
  await signIn(friend, FRIEND, FRIEND_PASSWORD);
  await expect(friend.getByRole("alert")).toHaveText(
    "Incorrect email or password.",
  );
  await signIn(friend, FRIEND, FRIEND_NEW_PASSWORD);
  await expect(menuButton(friend, FRIEND_NAME)).toBeVisible();

  await admin.reload();
  await expect(rowFor(admin, FRIEND)).toContainText("Used");
  expect(adminErrors).toEqual([]);
});
