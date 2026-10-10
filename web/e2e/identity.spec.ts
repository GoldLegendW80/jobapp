import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  expect,
  test,
  type Browser,
  type ConsoleMessage,
  type Page,
} from "@playwright/test";

import { clearAttempts } from "./stack";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const evidence = path.join(
  root,
  "tasks/evidence",
  process.env.E2E_EVIDENCE ?? "identity-i3",
);
const prefix = (process.env.E2E_SHOT_PREFIX ?? "").replace(
  "{spec}",
  "identity",
);

/**
 * The identity journey against the real stack through Caddy: no mocks at all.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8093 docker compose -p jobapp-i3 -f compose.yml up -d --build --wait
 *   docker compose -p jobapp-i3 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8093 E2E_COMPOSE_PROJECT=jobapp-i3 \
 *     pnpm --dir web exec playwright test identity.spec.ts
 *   docker compose -p jobapp-i3 -f compose.yml down -v
 * The admin link comes from the CLI in the api container of that project.
 *
 * On the Jetson, through the public Funnel URL (Checkpoint I-A):
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_SHOT_PREFIX=jetson- pnpm --dir web exec playwright test identity.spec.ts
 * The link then comes from `./deploy.sh admin create <email>`, which prints the public link.
 * Remove the test account afterwards (README, Accounts).
 *
 * Run it on its own, not next to caddy.spec.ts, which stops the database while it runs.
 */
const caddyUrl = process.env.E2E_CADDY_URL;
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EMAIL =
  process.env.E2E_ADMIN_EMAIL ??
  `e2e-admin-${Date.now().toString(36)}@example.org`;
const NAME = "E2E Admin";
// An address with no account, for the generic error: an e2e one so its attempt is cleaned up.
const UNKNOWN = `e2e-nobody-${Date.now().toString(36)}@example.org`;
// Fresh per run, never a common password, never logged.
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const NEW_PASSWORD = `Pw2 ${randomBytes(12).toString("base64url")}`;
const LINK = /\/(invite|reset)\/[A-Za-z0-9_-]{43}/;

/** A one-time link from the operator CLI, as a path to open on the base URL. */
function adminLink(email: string): { path: string; printed: string } {
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
  const printed = /https?:\/\/\S+/.exec(output)?.[0] ?? "";
  const match = LINK.exec(printed);
  if (!match) throw new Error("the CLI printed no link");
  return { path: match[0], printed };
}

/** Console errors, except the browser's own lines for the expected 401, 404 and 422 answers. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() !== "error") return;
    const text = `${msg.text()} @ ${msg.location().url}`;
    if (/status of 401/.test(text) && /\/api\/auth\/(me|login)/.test(text))
      return;
    if (/status of 404/.test(text) && /\/api\/auth\/invites\//.test(text))
      return;
    // The common password refused on purpose by the journey.
    if (
      /status of 422/.test(text) &&
      /\/api\/auth\/invites\/.*\/accept/.test(text)
    )
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
async function shots(page: Page, name: string) {
  fs.mkdirSync(evidence, { recursive: true });
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    for (const scheme of SCHEMES) {
      await page.emulateMedia({ colorScheme: scheme });
      // Nothing scrolls sideways at 375px.
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
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light" });
}

const menuButton = (page: Page, name = NAME) =>
  page.getByRole("button", { name: `Account menu, ${name}` });

async function signIn(page: Page, email: string, password: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function signOut(page: Page) {
  await menuButton(page).click();
  await expect(page.getByRole("menu")).toContainText(EMAIL);
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(
    "/login",
  );
}

async function freshPage(browser: Browser) {
  const context = await browser.newContext({ baseURL: caddyUrl });
  return { context, page: await context.newPage() };
}

let used = ""; // the invite path, once accepted

test.afterAll(() => {
  clearAttempts([EMAIL.toLowerCase(), UNKNOWN]);
});

test("signed out, / goes to the sign in page with next=/", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page).toHaveURL(/\/login\?next=\/$/);
  await expect(page).toHaveTitle("Sign in · jobapp");
  await shots(page, "login");
  expect(errors).toEqual([]);
});

test("the CLI link sets up the admin, who lands signed in on /", async ({
  page,
  context,
}) => {
  const errors = watchErrors(page);
  const link = adminLink(EMAIL);
  if (caddyUrl && process.env.E2E_ADMIN_VIA_DEPLOY)
    expect(link.printed.startsWith(caddyUrl)).toBe(true);
  await page.goto(link.path);
  // A first run sets up the account; on a rerun for an existing admin the CLI prints a reset.
  const isInvite = link.path.startsWith("/invite/");
  await expect(
    page.getByRole("heading", {
      name: isInvite ? "Set up your account" : "Choose a new password",
    }),
  ).toBeVisible();
  expect((await page.getByLabel("Email").inputValue()).toLowerCase()).toBe(
    EMAIL.toLowerCase(),
  );
  await expect(page.getByLabel("Email")).toHaveAttribute("readonly", "");
  await shots(page, isInvite ? "invite" : "reset-first");

  // Client side check first: a mismatch sends nothing.
  if (isInvite) await page.getByLabel("Name").fill(NAME);
  await page.getByLabel(/^(New password|Password)$/).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(`${PASSWORD}x`);
  await page
    .getByRole("button", { name: /Create account|Save password/ })
    .click();
  await expect(page.getByText("The passwords do not match.")).toBeVisible();
  await expect(page.getByLabel("Confirm password")).toBeFocused();

  // The api's own rule: a common password is refused with its message, shown as is.
  await page.getByLabel(/^(New password|Password)$/).fill("password123");
  await page.getByLabel("Confirm password").fill("password123");
  await page
    .getByRole("button", { name: /Create account|Save password/ })
    .click();
  await expect(
    page.getByText(
      "This password is too common. Choose one that is harder to guess.",
    ),
  ).toBeVisible();

  await page.getByLabel(/^(New password|Password)$/).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page
    .getByRole("button", { name: /Create account|Save password/ })
    .click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(menuButton(page)).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main" }).getByRole("link", {
      name: "Admin",
    }),
  ).toBeVisible();
  await expect(page.getByRole("contentinfo").getByRole("status")).toContainText(
    "API ok",
  );
  used = link.path;

  const cookie = (await context.cookies()).find(
    (c) => c.name === "jobapp_session",
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(cookie?.secure).toBe(caddyUrl?.startsWith("https://") ?? false);
  await shots(page, "shell-admin");
  await menuButton(page).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({
    path: path.join(evidence, `${prefix}shell-menu-mobile-light.png`),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});

test("sign out lands on /login and the back button shows nothing protected", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await page.goto("/login");
  await signIn(page, EMAIL, PASSWORD);
  await expect(menuButton(page)).toBeVisible();
  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  await signOut(page);
  // History: /login was replaced by /jobs on sign in (/ redirects there), then /admin/users by
  // /login on sign out. Back reaches /jobs, which must not render: it sends to sign in again.
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page).toHaveURL(/\/login\?next=\/jobs$/);
  await expect(page.getByRole("banner")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Jobs" })).toHaveCount(0);
  // The session is gone on the server too: a reload stays signed out.
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/login\?next=\/admin\/users$/);
  expect(errors).toEqual([]);
});

test("sign in with a mixed case email, reload keeps the session, deep link as admin", async ({
  page,
  browser,
}) => {
  const errors = watchErrors(page);
  const mixed = EMAIL.replace(/^e2e-admin/, "E2E-Admin").replace(
    "example.org",
    "Example.ORG",
  );
  expect(mixed).not.toBe(EMAIL);
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/login\?next=\/admin\/users$/);
  await signIn(page, mixed, PASSWORD);
  // Back to the page asked for.
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/users$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  await expect(menuButton(page)).toBeVisible();
  await page.goto("/");
  await expect(menuButton(page)).toBeVisible();

  // Another browser without the cookie is not signed in.
  const other = await freshPage(browser);
  try {
    await other.page.goto("/");
    await expect(
      other.page.getByRole("heading", { name: "Sign in" }),
    ).toBeVisible();
    await expect(other.page).toHaveURL(/\/login\?next=\/$/);
  } finally {
    await other.context.close();
  }

  // Signed in, /login sends back home.
  await page.goto("/login");
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(menuButton(page)).toBeVisible();
  expect(errors).toEqual([]);
});

test("wrong password and unknown email get the same generic error", async ({
  page,
}) => {
  await page.goto("/login");
  await signIn(page, EMAIL, `${PASSWORD}wrong`);
  const alert = page.getByRole("alert");
  await expect(alert).toHaveText("Incorrect email or password.");
  await expect(page).toHaveURL(/\/login$/);
  await shots(page, "login-error");
  await signIn(page, UNKNOWN, PASSWORD);
  await expect(alert).toHaveText("Incorrect email or password.");
  // An unsafe next is ignored after a good sign in.
  await page.goto("/login?next=//evil.example");
  await signIn(page, EMAIL, PASSWORD);
  await expect(page).toHaveURL(new RegExp(`^${caddyUrl ?? ""}/jobs$`));
  await expect(menuButton(page)).toBeVisible();
});

test("invalid, malformed and used links show the invalid page", async ({
  page,
}) => {
  const errors = watchErrors(page);
  const unknown = randomBytes(32).toString("base64url");
  await page.goto(`/invite/${unknown}`);
  await expect(
    page.getByRole("heading", { name: "This link is invalid or expired" }),
  ).toBeVisible();
  await shots(page, "invalid-link");
  for (const path of [`/reset/${unknown}`, "/invite/not-a-token", used]) {
    if (!path) continue;
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: "This link is invalid or expired" }),
    ).toBeVisible();
  }
  await page.getByRole("link", { name: "Go to sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("a reset link from the CLI sets a new password and ends the old session", async ({
  page,
  browser,
}) => {
  const errors = watchErrors(page);
  // Signed in in a second browser first: the reset must end that session.
  const other = await freshPage(browser);
  await other.page.goto("/login");
  await signIn(other.page, EMAIL, PASSWORD);
  await expect(menuButton(other.page)).toBeVisible();

  const link = adminLink(EMAIL);
  expect(link.path).toMatch(/^\/reset\//);
  await page.goto(link.path);
  await expect(
    page.getByRole("heading", { name: "Choose a new password" }),
  ).toBeVisible();
  await expect(page.getByLabel("Name")).toHaveCount(0);
  await shots(page, "reset");
  await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Confirm password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(menuButton(page)).toBeVisible();

  // The other browser's session ended with the reset: it lands on /login, keeping its page.
  await other.page.goto("/admin/users");
  await expect(other.page).toHaveURL(/\/login\?next=\/admin\/users$/);
  await other.context.close();

  await signOut(page);
  await signIn(page, EMAIL, PASSWORD);
  await expect(page.getByRole("alert")).toHaveText(
    "Incorrect email or password.",
  );
  await signIn(page, EMAIL, NEW_PASSWORD);
  await expect(menuButton(page)).toBeVisible();
  await signOut(page);
  expect(errors).toEqual([]);
});
