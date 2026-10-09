import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page, type Route } from "@playwright/test";

const evidence = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../tasks/evidence",
  process.env.E2E_EVIDENCE ?? "task5",
);

const HEALTH = "**/api/health";

/** A signed in user for every test: the home page is protected since identity I3. */
test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    json(route, 200, {
      id: "00000000-0000-4000-8000-000000000001",
      email: "harald@example.org",
      name: "Harald Hautin",
      role: "admin",
      created_at: "2026-10-08T10:00:00Z",
      last_login_at: null,
    }),
  );
  // The home page is the jobs dashboard: an empty list, so only the health check is under test.
  await page.route(/\/api\/jobs(\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        next_cursor: null,
        total: 0,
        counts_by_status: {},
        fit_histogram: { buckets: [], unscored: 0 },
      }),
    }),
  );
});

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Count health requests; each call of `respond` decides the answer for that request. */
async function mockHealth(
  page: Page,
  respond: (route: Route, call: number) => Promise<void>,
) {
  const calls = { count: 0 };
  await page.route(HEALTH, async (route) => {
    calls.count += 1;
    await respond(route, calls.count);
  });
  return calls;
}

const status = (page: Page) =>
  page.getByRole("contentinfo").getByRole("status");
const retry = (page: Page) =>
  page.getByRole("button", { name: "Check the API again" });

test.describe("API status in the footer", () => {
  test('shows "API ok" when the api answers 200', async ({ page }) => {
    const calls = await mockHealth(page, (route) =>
      json(route, 200, { status: "ok" }),
    );
    await page.goto("/");
    await expect(page.getByRole("link", { name: "jobapp" })).toBeVisible();
    await expect(status(page)).toContainText("API ok");
    await expect(retry(page)).toBeEnabled();
    expect(calls.count).toBe(1);
  });

  test('shows "API unavailable" on 503 and the reason', async ({ page }) => {
    await mockHealth(page, (route) =>
      json(route, 503, { status: "error", detail: "database unavailable" }),
    );
    await page.goto("/");
    await expect(status(page)).toContainText("API unavailable");
    await expect(status(page)).toContainText(
      "The API answered 503: database unavailable.",
    );
  });

  test('shows "API unavailable" when the connection fails', async ({
    page,
  }) => {
    await mockHealth(page, (route) => route.abort("connectionrefused"));
    await page.goto("/");
    await expect(status(page)).toContainText("API unavailable");
    await expect(status(page)).toContainText("The API could not be reached.");
  });

  test("shows Checking during a slow answer, then API ok", async ({ page }) => {
    await mockHealth(page, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await json(route, 200, { status: "ok" });
    });
    await page.goto("/");
    await expect(status(page)).toContainText("Checking");
    await expect(retry(page)).toBeDisabled();
    await expect(status(page)).toContainText("API ok", { timeout: 5000 });
  });

  test("gives up after the five second timeout", async ({ page }) => {
    test.setTimeout(20_000);
    await mockHealth(page, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 8000));
      await json(route, 200, { status: "ok" }).catch(() => {});
    });
    const started = Date.now();
    await page.goto("/");
    await expect(status(page)).toContainText(
      "The API did not answer in time.",
      {
        timeout: 7000,
      },
    );
    expect(Date.now() - started).toBeGreaterThanOrEqual(4500);
  });

  test("Retry recovers after a failure and does not loop", async ({ page }) => {
    const calls = await mockHealth(page, (route, call) =>
      call === 1
        ? json(route, 503, { status: "error", detail: "database unavailable" })
        : json(route, 200, { status: "ok" }),
    );
    await page.goto("/");
    await expect(status(page)).toContainText("API unavailable");
    await page.waitForTimeout(1500);
    expect(calls.count).toBe(1);

    await retry(page).click();
    await expect(status(page)).toContainText("API ok");
    await page.waitForTimeout(1000);
    expect(calls.count).toBe(2);
  });

  test("Retry works from the keyboard", async ({ page }) => {
    await mockHealth(page, (route, call) =>
      call === 1 ? route.abort("failed") : json(route, 200, { status: "ok" }),
    );
    await page.goto("/");
    await expect(status(page)).toContainText("API unavailable");
    await retry(page).focus();
    await expect(retry(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(status(page)).toContainText("API ok");
  });

  test("does not refetch when the tab regains focus", async ({ page }) => {
    const calls = await mockHealth(page, (route) =>
      json(route, 503, { status: "error" }),
    );
    await page.goto("/");
    await expect(status(page)).toContainText("API unavailable");
    await page.evaluate(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("online"));
    });
    await page.waitForTimeout(1000);
    expect(calls.count).toBe(1);
  });

  test("serves the shell with styles applied and no console errors", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await mockHealth(page, (route) => json(route, 200, { status: "ok" }));
    await page.goto("/");
    await expect(status(page)).toContainText("API ok");
    // Tailwind and the theme tokens are applied: the ok dot is the success green, and the
    // check again button is fully opaque once transitions settle.
    const dot = status(page).locator("span").first();
    await expect(dot).toHaveCSS(
      "background-color",
      "oklch(0.527 0.154 150.069)",
    );
    expect(
      await dot.evaluate((el) => getComputedStyle(el).borderRadius),
    ).not.toBe("0px");
    await expect(retry(page)).toHaveCSS("opacity", "1");
    expect(errors).toEqual([]);
  });
});

test.describe("themes and viewports", () => {
  const viewports = [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 375, height: 812 },
  ] as const;
  const schemes = ["light", "dark"] as const;

  for (const viewport of viewports) {
    for (const scheme of schemes) {
      test(`${viewport.name} ${scheme}`, async ({ page }) => {
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height,
        });
        await page.emulateMedia({ colorScheme: scheme });
        await mockHealth(page, (route) => json(route, 200, { status: "ok" }));
        await page.goto("/");
        await expect(status(page)).toContainText("API ok");

        const background = await page.evaluate(
          () => getComputedStyle(document.body).backgroundColor,
        );
        const lightness = await page.evaluate(() => {
          const probe = document.createElement("div");
          probe.style.color = getComputedStyle(document.body).backgroundColor;
          document.body.appendChild(probe);
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 1;
          const context = canvas.getContext("2d")!;
          context.fillStyle = getComputedStyle(probe).color;
          context.fillRect(0, 0, 1, 1);
          probe.remove();
          const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
          return (0.2126 * r! + 0.7152 * g! + 0.0722 * b!) / 255;
        });
        if (scheme === "dark") expect(lightness, background).toBeLessThan(0.3);
        else expect(lightness, background).toBeGreaterThan(0.8);

        const overflow = await page.evaluate(
          () =>
            document.documentElement.scrollWidth -
            document.documentElement.clientWidth,
        );
        expect(overflow).toBe(0);

        await page.screenshot({
          path: path.join(evidence, `home-${viewport.name}-${scheme}.png`),
          fullPage: true,
          animations: "disabled",
        });
      });
    }
  }

  for (const scheme of schemes) {
    test(`unavailable state, mobile ${scheme}`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 });
      await page.emulateMedia({ colorScheme: scheme });
      await mockHealth(page, (route) =>
        json(route, 503, { status: "error", detail: "database unavailable" }),
      );
      await page.goto("/");
      await expect(status(page)).toContainText("API unavailable");
      await page.screenshot({
        path: path.join(evidence, `home-mobile-${scheme}-unavailable.png`),
        fullPage: true,
        animations: "disabled",
      });
    });
  }
});
