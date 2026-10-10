import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  createLink as createLinkWith,
  makeShots,
  menuButton,
  newContext,
  rowFor,
  setPassword,
  signIn,
  signOut,
  watchErrors,
} from "./stack";

const shots = makeShots("identity-i4", "invites");

/**
 * Invites from the admin area, against the real stack through Caddy: no mocks.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8094 docker compose -p jobapp-i4 -f compose.yml -f compose.mac.yml up -d --build --wait
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
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

// Unique per spec file, so two specs of one run never share a friend.
const run = `${Date.now().toString(36)}${randomBytes(2).toString("hex")}`;
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const FRIEND = process.env.E2E_FRIEND_EMAIL ?? `e2e-friend-${run}@example.org`;
const ADMIN_NAME = "E2E Admin";
const FRIEND_NAME = "E2E Friend";
// Fresh per run, never a common password, never logged.
const ADMIN_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const FRIEND_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const FRIEND_NEW_PASSWORD = `Pw2 ${randomBytes(12).toString("base64url")}`;

/** Create a link from the dialog, with screenshots of the result when `shot` is set. */
const createLink = (
  page: Page,
  email: string,
  kind: "invite" | "reset",
  shot?: string,
) =>
  createLinkWith(page, email, kind, shot ? { shots, name: shot } : undefined);

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
  clearAttempts([ADMIN, FRIEND]);
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
