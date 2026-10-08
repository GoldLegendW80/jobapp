import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const evidence = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../tasks/evidence/task5",
);

/**
 * Against the real api, no mocks: the preview server proxies /api to VITE_API_PROXY_TARGET.
 * Opt in with E2E_REAL_API=up once the api and its database run, or E2E_REAL_API=db-down once
 * the api runs and its database is stopped.
 */
const mode = process.env.E2E_REAL_API;

// The health card is on the protected home page: fake the session only, the health check is real.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "00000000-0000-4000-8000-000000000001",
        email: "real-api@example.org",
        name: "Real Api",
        role: "member",
        created_at: "2026-10-08T10:00:00Z",
        last_login_at: null,
      }),
    }),
  );
});

test.describe("real api, database up", () => {
  test.skip(
    mode !== "up",
    "set E2E_REAL_API=up with the api and database running",
  );

  test('the page shows "API ok" from the running api', async ({ page }) => {
    const response = page.waitForResponse("**/api/health");
    await page.goto("/");
    expect((await response).status()).toBe(200);
    await expect(page.getByRole("status")).toContainText("API ok");
    await page.screenshot({
      path: path.join(evidence, "real-api-ok.png"),
      fullPage: true,
      animations: "disabled",
    });
  });

  test("the proxied health answer is the api's own body", async ({
    request,
  }) => {
    const response = await request.get("/api/health");
    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  });
});

test.describe("real api, database down", () => {
  test.skip(
    mode !== "db-down",
    "set E2E_REAL_API=db-down with the database stopped",
  );

  test('the page shows "API unavailable" from the real 503', async ({
    page,
  }) => {
    const response = page.waitForResponse("**/api/health");
    await page.goto("/");
    expect((await response).status()).toBe(503);
    await expect(page.getByRole("status")).toContainText("API unavailable");
    await expect(page.getByRole("status")).toContainText(
      "The API answered 503: database unavailable.",
    );
    await page.screenshot({
      path: path.join(evidence, "real-api-db-down.png"),
      fullPage: true,
      animations: "disabled",
    });
  });
});
