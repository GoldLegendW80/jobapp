import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  cliLink,
  createLink,
  makeShots,
  menuButton,
  newContext,
  setPassword,
  signIn,
  watchErrors,
} from "./stack";

const shots = makeShots("identity-i5");

/**
 * The accounts table of the admin area, against the real stack through Caddy: no mocks.
 * Disable and enable, promote and demote, demoting yourself, and the last admin rule.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8095 docker compose -p jobapp-i5 -f compose.yml up -d --build --wait
 *   docker compose -p jobapp-i5 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-i5 \
 *     pnpm --dir web exec playwright test users.spec.ts
 *   docker compose -p jobapp-i5 -f compose.yml down -v
 *
 * On the Jetson, through the public URL, with throwaway accounts removed afterwards (README).
 * Other accounts may exist there: the spec only ever acts on the rows of its two emails, and
 * checks the last admin rule only when its admin is the only active one.
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_FRIEND_EMAIL=e2e-friend@example.org E2E_SHOT_PREFIX=jetson- \
 *     pnpm --dir web exec playwright test users.spec.ts
 */
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
const LAST_ADMIN =
  "This is the last active admin. Make someone else an admin first, then try again.";

const accounts = (page: Page) =>
  page.getByRole("table", { name: "Accounts, by name" });

/** The accounts row of one of this spec's emails; never anyone else's. */
const userRow = (page: Page, email: string) =>
  accounts(page).getByRole("row").filter({ hasText: email });

const adminLink = (page: Page) =>
  page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Admin" });

async function openMenu(page: Page, email: string) {
  await userRow(page, email)
    .getByRole("button", { name: /^Actions for / })
    .click();
}

/** Pick an action on a row; returns the confirm dialog. */
async function choose(page: Page, email: string, item: string) {
  await openMenu(page, email);
  await page.getByRole("menuitem", { name: item }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

async function confirm(page: Page, button: string, toast: RegExp | string) {
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: button })
    .click();
  await expect(page.getByText(toast)).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
}

async function openAdmin(page: Page) {
  await page.goto("/admin/users");
  await expect(accounts(page)).toBeVisible();
}

let adminContext: BrowserContext;
let admin: Page;
let adminErrors: string[];
let friendContext: BrowserContext;
let friend: Page;
let friendErrors: string[];
let friendCookiesBeforeDisable: Awaited<ReturnType<BrowserContext["cookies"]>> =
  [];
let adminName = ADMIN_NAME;

test.beforeAll(async ({ browser }) => {
  adminContext = await newContext(browser);
  admin = await adminContext.newPage();
  adminErrors = watchErrors(admin, [[409, /\/api\/admin\/users/]]);
  friendContext = await newContext(browser);
  friend = await friendContext.newPage();
  friendErrors = watchErrors(friend);
});

test.afterAll(async () => {
  await adminContext.close();
  await friendContext.close();
});

test("the admin invites a friend, who accepts in another browser", async () => {
  const link = cliLink(ADMIN);
  await admin.goto(link);
  // A first run sets up the account; a rerun for an existing admin gets a reset link.
  const fresh = link.startsWith("/invite/");
  await setPassword(admin, ADMIN_PASSWORD, fresh ? ADMIN_NAME : undefined);
  await openAdmin(admin);
  if (!fresh) {
    const shown = await userRow(admin, ADMIN)
      .getByRole("rowheader")
      .innerText();
    adminName = shown.split("\n")[0]?.trim() ?? ADMIN_NAME;
  }

  const invite = await createLink(admin, FRIEND, "invite");
  await friend.goto(invite);
  await setPassword(friend, FRIEND_PASSWORD, FRIEND_NAME);
  await expect(menuButton(friend, FRIEND_NAME)).toBeVisible();
  expect(adminErrors).toEqual([]);
});

test("the admin sees every account above the invites", async () => {
  await admin.reload();
  const headings = admin.getByRole("heading", { level: 2 });
  await expect(headings).toHaveText(["Accounts", "Invites"]);

  const own = userRow(admin, ADMIN);
  await expect(own).toContainText("You");
  await expect(own).toContainText("Admin");
  await expect(own).toContainText("Active");

  const row = userRow(admin, FRIEND);
  await expect(row).toContainText(FRIEND_NAME);
  await expect(row).toContainText("Member");
  await expect(row).toContainText("Active");
  await expect(row).toContainText("now");
  await expect(row.getByRole("cell").nth(3)).toHaveText("1"); // sessions
  await expect(row).not.toContainText("You");
  await shots(admin, "admin-users");
  expect(adminErrors).toEqual([]);
});

test("disabling the friend sends their next navigation to the sign in page", async () => {
  friendCookiesBeforeDisable = await friendContext.cookies();

  // Cancel first: nothing happens.
  const dialog = await choose(admin, FRIEND, "Disable");
  await expect(dialog).toContainText(`Disable ${FRIEND_NAME}?`);
  await expect(dialog).toContainText("signed out of every browser at once");
  await shots(admin, "admin-disable-confirm", false);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(userRow(admin, FRIEND)).toContainText("Active");

  await choose(admin, FRIEND, "Disable");
  await confirm(
    admin,
    "Disable",
    `${FRIEND_NAME} is disabled and signed out everywhere.`,
  );
  const row = userRow(admin, FRIEND);
  await expect(row).toContainText("Disabled");
  await expect(row).toHaveAttribute("data-disabled", "true");
  await expect(row.getByRole("cell").nth(3)).toHaveText("0");
  // The toast would cover the cards at 375px.
  await expect(admin.getByText(/disabled and signed out/)).toBeHidden({
    timeout: 15_000,
  });
  await shots(admin, "admin-users-disabled");

  // The friend's browser still shows the app; its very next page load lands on /login.
  await friend.goto("/");
  await expect(friend).toHaveURL(/\/login/);
  await expect(friend.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await signIn(friend, FRIEND, FRIEND_PASSWORD);
  await expect(friend.getByRole("alert")).toHaveText(
    "Incorrect email or password.",
  );
  await shots(friend, "friend-disabled-sign-in");
  expect(adminErrors).toEqual([]);
});

test("re-enabling lets the friend sign in again, and the old browser stays signed out", async () => {
  await choose(admin, FRIEND, "Enable");
  await confirm(admin, "Enable", `${FRIEND_NAME} can sign in again.`);
  await expect(userRow(admin, FRIEND)).toContainText("Active");

  // A browser that still holds the cookie from before the disable is not let back in.
  const stale = await adminContext.browser()!.newContext({ baseURL: caddyUrl });
  await stale.addCookies(friendCookiesBeforeDisable);
  const old = await stale.newPage();
  await old.goto("/");
  await expect(old).toHaveURL(/\/login/);
  await stale.close();

  await signIn(friend, FRIEND, FRIEND_PASSWORD);
  await expect(menuButton(friend, FRIEND_NAME)).toBeVisible();
  await expect(adminLink(friend)).toHaveCount(0);
  expect(adminErrors).toEqual([]);
});

test("promoting the friend shows them the Admin entry after a reload", async () => {
  await choose(admin, FRIEND, "Make admin");
  await confirm(admin, "Make admin", `${FRIEND_NAME} is now an admin.`);
  await expect(userRow(admin, FRIEND)).toContainText("Admin");

  await friend.reload();
  await expect(adminLink(friend)).toBeVisible();
  await adminLink(friend).click();
  await expect(accounts(friend)).toBeVisible();
  await expect(userRow(friend, FRIEND)).toContainText("You");
  await shots(friend, "friend-admin-users");
});

test("the friend demotes the original admin, then restores them", async () => {
  const dialog = await choose(friend, ADMIN, "Make member");
  await expect(dialog).toContainText(`Make ${adminName} a member?`);
  await confirm(friend, "Make member", `${adminName} is now a member.`);

  await admin.goto("/");
  await expect(menuButton(admin, adminName)).toBeVisible();
  await expect(adminLink(admin)).toHaveCount(0);
  await admin.goto("/admin/users");
  await expect(
    admin.getByRole("heading", { name: "Page not found" }),
  ).toBeVisible();
  await shots(admin, "admin-demoted-404");

  await choose(friend, ADMIN, "Make admin");
  await confirm(friend, "Make admin", `${adminName} is now an admin.`);
  await admin.goto("/");
  await expect(adminLink(admin)).toBeVisible();
  expect(adminErrors).toEqual([]);
});

test("the admin demotes themselves after a confirmation and the Admin entry goes", async () => {
  await openAdmin(admin);
  // No Disable on your own row: another admin does that.
  await openMenu(admin, ADMIN);
  await expect(admin.getByRole("menuitem")).toHaveText(["Make member"]);
  await admin.keyboard.press("Escape");

  const dialog = await choose(admin, ADMIN, "Make member");
  await expect(dialog).toContainText("Give up your admin role?");
  await shots(admin, "admin-self-demote-confirm", false);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);

  await choose(admin, ADMIN, "Make member");
  await confirm(admin, "Make me a member", "You are now a member.");
  await expect(admin).toHaveURL(/\/jobs$/);
  await expect(adminLink(admin)).toHaveCount(0);

  // The friend, still an admin, restores them.
  await friend.reload();
  await choose(friend, ADMIN, "Make admin");
  await confirm(friend, "Make admin", `${adminName} is now an admin.`);
  await admin.reload();
  await expect(adminLink(admin)).toBeVisible();
  expect(adminErrors).toEqual([]);
});

test("the friend goes back to member, and the last active admin is protected", async () => {
  await openAdmin(admin);
  await choose(admin, FRIEND, "Make member");
  await confirm(admin, "Make member", `${FRIEND_NAME} is now a member.`);
  await friend.reload();
  await expect(adminLink(friend)).toHaveCount(0);

  const activeAdmins = await admin.evaluate(async () => {
    const response = await fetch("/api/admin/users?limit=1");
    return ((await response.json()) as { active_admins: number }).active_admins;
  });
  test.skip(
    activeAdmins !== 1,
    `${activeAdmins} active admins here: the last admin rule needs this admin alone`,
  );

  await openMenu(admin, ADMIN);
  await expect(
    admin.getByRole("menuitem", { name: "Make member" }),
  ).toHaveAttribute("aria-disabled", "true");
  await expect(
    admin.getByText(
      "The last active admin can be neither disabled nor made a member.",
    ),
  ).toBeVisible();
  await shots(admin, "admin-last-admin", false);
  await admin.keyboard.press("Escape");

  // The api refuses it too, whatever the client shows.
  const refused = await admin.evaluate(async (email) => {
    const list = (await (await fetch("/api/admin/users")).json()) as {
      items: Array<{ id: string; email: string }>;
    };
    const me = list.items.find((u) => u.email === email);
    const response = await fetch(`/api/admin/users/${me?.id ?? ""}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "jobapp",
      },
      body: JSON.stringify({ is_active: false }),
    });
    return {
      status: response.status,
      body: (await response.json()) as unknown,
    };
  }, ADMIN);
  expect(refused).toEqual({ status: 409, body: { detail: LAST_ADMIN } });
  await admin.reload();
  await expect(userRow(admin, ADMIN)).toContainText("Active");
  expect(adminErrors).toEqual([]);
  expect(friendErrors).toEqual([]);
});
