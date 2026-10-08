import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const evidence = path.join(
  root,
  "tasks/evidence",
  process.env.E2E_EVIDENCE ?? "task6",
);

/**
 * The real stack through Caddy, no mocks: static build, SPA fallback and /api proxy.
 * Opt in with E2E_CADDY_URL (for example http://127.0.0.1) once the Compose stack runs:
 *   docker compose -f compose.yml -f compose.dev.yml up -d --build --wait
 *   E2E_CADDY_URL=http://127.0.0.1 pnpm --dir web exec playwright test caddy.spec.ts
 * The test stops and starts the db service, so it runs serially.
 *
 * Against the Jetson, through an ssh tunnel, with compose driven over ssh (compose.yml only):
 *   ssh -N -L 18080:127.0.0.1:80 jetson &
 *   E2E_CADDY_URL=http://127.0.0.1:18080 E2E_COMPOSE_SSH=jetson E2E_EVIDENCE=task4 \
 *     pnpm --dir web exec playwright test caddy.spec.ts
 * E2E_COMPOSE_DIR names the deploy directory on that host (default jobapp, under its home).
 *
 * Through the public quick tunnel (from any network, Cloudflare in front):
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_COMPOSE_SSH=jetson E2E_EVIDENCE=task8 \
 *     pnpm --dir web exec playwright test caddy.spec.ts
 */
const caddyUrl = process.env.E2E_CADDY_URL;
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const sshHost = process.env.E2E_COMPOSE_SSH;
const remoteDir = process.env.E2E_COMPOSE_DIR ?? "jobapp";
const COMPOSE = ["compose", "-f", "compose.yml", "-f", "compose.dev.yml"];
function compose(...args: string[]) {
  if (sshHost) {
    // Service names only, no shell metacharacters: safe to join for the remote shell.
    const remote = `cd ${remoteDir} && docker compose -f compose.yml ${args.join(" ")}`;
    execFileSync("ssh", ["-o", "BatchMode=yes", sshHost, remote], {
      stdio: "ignore",
    });
    return;
  }
  execFileSync("docker", [...COMPOSE, ...args], { cwd: root, stdio: "ignore" });
}

const status = (page: Page) => page.getByRole("status");
const retry = (page: Page) => page.getByRole("button", { name: "Retry" });

/** Console errors and uncaught page errors, for the whole test. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error")
      errors.push(`${msg.text()} @ ${msg.location().url}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(evidence, `${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

test.afterAll(() => {
  // Never leave a service stopped, whatever failed.
  compose("start", "db", "api");
});

test('the app loads through Caddy and shows "API ok"', async ({ page }) => {
  const errors = watchErrors(page);
  const health = page.waitForResponse("**/api/health");
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  expect(response?.headers()["x-frame-options"]).toBe("DENY");
  expect((await health).status()).toBe(200);
  expect((await health).headers()["via"]).toContain("Caddy");
  await expect(
    page.getByRole("heading", { level: 1, name: "jobapp" }),
  ).toBeVisible();
  await expect(status(page)).toContainText("API ok");
  await shot(page, "1-api-ok");
  expect(errors).toEqual([]);
});

test("database stopped, Retry shows the 503, database started, Retry recovers", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(status(page)).toContainText("API ok");

  compose("stop", "db");
  const down = page.waitForResponse("**/api/health");
  await retry(page).click();
  expect((await down).status()).toBe(503);
  await expect(status(page)).toContainText("API unavailable");
  await expect(status(page)).toContainText(
    "The API answered 503: database unavailable.",
  );
  await shot(page, "2-db-down");

  compose("start", "db");
  // The database takes a few seconds to accept connections again: retry like a user would.
  await expect(async () => {
    await retry(page).click();
    await expect(status(page)).toContainText("API ok", { timeout: 2_000 });
  }).toPass({ timeout: 60_000, intervals: [1_000, 2_000] });
  await shot(page, "3-db-back");

  // The only console errors allowed are the browser's own lines for the 503 answers.
  const unexpected = errors.filter(
    (e) => !/Failed to load resource: .* 503 .*\/api\/health/.test(e),
  );
  expect(unexpected).toEqual([]);
});

test("api stopped: the shell still loads and the card shows Caddy's 502", async ({
  page,
}) => {
  compose("stop", "api");
  try {
    const health = page.waitForResponse("**/api/health");
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    expect((await health).status()).toBe(502);
    await expect(status(page)).toContainText("API unavailable");
    await expect(status(page)).toContainText("502");
    await shot(page, "5-api-stopped-502");
  } finally {
    compose("start", "api");
  }
  await expect(async () => {
    await retry(page).click();
    await expect(status(page)).toContainText("API ok", { timeout: 2_000 });
  }).toPass({ timeout: 60_000, intervals: [1_000, 2_000] });
});

test("a deep link loads and survives a reload", async ({ page }) => {
  const errors = watchErrors(page);
  const response = await page.goto("/some/deep/route?x=1");
  expect(response?.status()).toBe(200);
  expect(response?.headers()["cache-control"]).toBe("no-cache");
  await expect(status(page)).toContainText("API ok");
  const reloaded = await page.reload();
  expect(reloaded?.status()).toBe(200);
  await expect(
    page.getByRole("heading", { level: 1, name: "jobapp" }),
  ).toBeVisible();
  await expect(status(page)).toContainText("API ok");
  expect(page.url()).toMatch(/\/some\/deep\/route\?x=1$/);
  await shot(page, "4-deep-link-reload");
  expect(errors).toEqual([]);
});

test("hashed assets are immutable and a missing one is a real 404", async ({
  page,
  request,
}) => {
  const scripts: string[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/assets/")) scripts.push(r.url());
  });
  await page.goto("/");
  await expect(status(page)).toContainText("API ok");
  expect(scripts.length).toBeGreaterThan(0);
  for (const url of scripts) {
    const r = await request.get(url);
    expect(r.status()).toBe(200);
    expect(r.headers()["cache-control"]).toBe(
      "public, max-age=31536000, immutable",
    );
  }
  const missing = await request.get("/assets/nope.js");
  expect(missing.status()).toBe(404);
  expect(await missing.text()).not.toContain('<div id="root">');
});
