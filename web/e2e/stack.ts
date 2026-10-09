/**
 * Helpers of the journeys that run against the real stack through Caddy (invites.spec.ts,
 * users.spec.ts): the operator CLI link, sign in and out, a second browser, screenshots in
 * light and dark at desktop and 375px, and creating a link from the admin area.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  expect,
  type Browser,
  type ConsoleMessage,
  type Locator,
  type Page,
} from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(here, "../..");
export const caddyUrl = process.env.E2E_CADDY_URL;
export const LINK = /\/(invite|reset)\/[A-Za-z0-9_-]{43}/;

/** A one-time link from the operator CLI, as a path to open on the base URL. */
export function cliLink(email: string): string {
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

/**
 * Delete the sign in attempts recorded for these emails, so the next spec or a rerun within 15
 * minutes is not throttled by this one: every local browser reaches the api from one address
 * (Caddy's peer), and every browser of the Mac from one address through the tunnel, which allows
 * 20 failures per 15 minutes. On a local scratch stack it deletes every row of the emails; on the
 * Jetson (E2E_ADMIN_VIA_DEPLOY) it runs `./deploy.sh admin unlock <email>`, which deletes their
 * failures in the window (tests/e2e_jetson.sh removes the rest at the end). Returns how many rows
 * went, or undefined when there is no stack to clean.
 */
export function clearAttempts(emails: string[]): number | undefined {
  for (const email of emails)
    if (!/^[a-z0-9.@-]+$/.test(email))
      throw new Error(`refusing to clear attempts of ${email}`);
  if (process.env.E2E_ADMIN_VIA_DEPLOY) {
    let removed = 0;
    for (const email of emails) {
      const output = execFileSync(
        path.join(root, "deploy.sh"),
        ["admin", "unlock", email],
        { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] },
      );
      removed += Number(/removed (\d+)/.exec(output)?.[1] ?? Number.NaN);
    }
    console.log(
      `   unlocked ${emails.join(", ")}: ${removed} failed attempt(s)`,
    );
    return removed;
  }
  if (!process.env.E2E_COMPOSE_PROJECT) return undefined;
  const list = emails.map((email) => `'${email}'`).join(", ");
  const output = execFileSync(
    "docker",
    [
      "compose",
      "-p",
      process.env.E2E_COMPOSE_PROJECT,
      "-f",
      "compose.yml",
      "exec",
      "-T",
      "db",
      "sh",
      "-c",
      'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$0"',
      `with gone as (delete from login_attempts where email in (${list}) returning 1) select count(*) from gone`,
    ],
    { cwd: root, encoding: "utf8" },
  );
  return Number(output.trim());
}

/** Answers the journeys expect, as the browser logs them: [status, url pattern]. */
const EXPECTED: Array<[number, RegExp]> = [
  [401, /\/api\/auth\/(me|login)/],
  [404, /\/api\/auth\/invites\//],
  [409, /\/api\/admin\/invites/],
];

/** Console errors, except the browser's own lines for answers the journey expects. */
export function watchErrors(page: Page, also: Array<[number, RegExp]> = []) {
  const errors: string[] = [];
  const expected = [...EXPECTED, ...also];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() !== "error") return;
    const text = `${msg.text()} @ ${msg.location().url}`;
    if (
      expected.some(
        ([status, url]) =>
          text.includes(`status of ${status}`) && url.test(text),
      )
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

export type Shots = (
  page: Page,
  name: string,
  fullPage?: boolean,
) => Promise<void>;

/**
 * One screenshot per viewport and colour scheme into tasks/evidence/<E2E_EVIDENCE or the
 * default>, each checked for horizontal overflow, then back to desktop light. Files are named
 * <E2E_SHOT_PREFIX><name>-<viewport>-<scheme>.png, with {spec} in the prefix replaced by `spec`,
 * so specs sharing one evidence folder never overwrite each other.
 */
export function makeShots(defaultEvidence: string, spec = ""): Shots {
  const evidence = path.join(
    root,
    "tasks/evidence",
    process.env.E2E_EVIDENCE ?? defaultEvidence,
  );
  const prefix = (process.env.E2E_SHOT_PREFIX ?? "").replace("{spec}", spec);
  return async (page, name, fullPage = true) => {
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
  };
}

export async function setPassword(page: Page, password: string, name?: string) {
  if (name !== undefined) await page.getByLabel("Name").fill(name);
  await page.getByLabel(/^(New password|Password)$/).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page
    .getByRole("button", { name: /Create account|Save password/ })
    .click();
  await expect(page).toHaveURL(/\/jobs$/);
}

export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export const menuButton = (page: Page, name: string) =>
  page.getByRole("button", { name: `Account menu, ${name}` });

export async function signOut(page: Page, name: string) {
  await menuButton(page, name).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

export async function newContext(browser: Browser) {
  const context = await browser.newContext({ baseURL: caddyUrl });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: caddyUrl,
  });
  return context;
}

/** The newest invite row for an email (the list is newest first). */
export const rowFor = (page: Page, email: string): Locator =>
  page
    .getByRole("table", { name: "Invites, newest first" })
    .getByRole("row")
    .filter({ hasText: email })
    .first();

/** Create a link from the dialog; returns the full link as shown. */
export async function createLink(
  page: Page,
  email: string,
  kind: "invite" | "reset",
  shot?: { shots: Shots; name: string },
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
  if (shot) await shot.shots(page, shot.name, false);
  await result.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Shown once: nowhere on the page any more.
  await expect(page.locator("body")).not.toContainText(new URL(link).pathname);
  return link;
}
