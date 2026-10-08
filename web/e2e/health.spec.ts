import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page, type Route } from "@playwright/test";

const evidence = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../tasks/evidence/task5",
);

const HEALTH = "**/api/health";

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

const status = (page: Page) => page.getByRole("status");
const retry = (page: Page) => page.getByRole("button", { name: "Retry" });

test.describe("API status card", () => {
  test('shows "API ok" when the api answers 200', async ({ page }) => {
    const calls = await mockHealth(page, (route) =>
      json(route, 200, { status: "ok" }),
    );
    await page.goto("/");
    await expect(
      page.getByRole("heading", { level: 1, name: "jobapp" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "API status" }),
    ).toBeVisible();
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
    await page.keyboard.press("Tab");
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
    const card = page.locator('[data-slot="card"]');
    // Tailwind and the shadcn Card styles are applied: rounded corners and a border.
    await expect(card).toHaveCSS("border-top-style", "solid");
    expect(
      await card.evaluate((el) => getComputedStyle(el).borderRadius),
    ).not.toBe("0px");
    // Once transitions settle the Retry button is fully opaque and the ok badge is green with
    // light text (the success tokens resolve).
    await expect(retry(page)).toHaveCSS("opacity", "1");
    const badge = page.locator('[data-slot="badge"]');
    await expect(badge).toHaveCSS(
      "background-color",
      "oklch(0.527 0.154 150.069)",
    );
    await expect(badge).toHaveCSS("color", "oklch(0.985 0 0)");
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
