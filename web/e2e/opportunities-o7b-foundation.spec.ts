import { expect, test, type Page } from "@playwright/test";

import { CSRF, caddyUrl, invented, signUp } from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

// Foundation A carries protected consent through Search. Activation and reading belong to B.
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const platform = "/api/opportunities/platforms/freework";
const shots = makeShots("opportunities-o7b-foundation", "o7b-foundation");
type Source = {
  kind: string;
  query: string;
  location: string | null;
  enabled: boolean;
  accepted_at: string | null;
  text_version: number | null;
};
type Profile = {
  revision: number;
  sections: { search: { browser_sources: Source[]; days: number } };
};
const generic = (query: string): Source => ({
  kind: "vie",
  query,
  location: "Paris",
  enabled: false,
  accepted_at: null,
  text_version: null,
});
async function read(page: Page): Promise<Profile> {
  const response = await page.request.get("/api/profile");
  expect(response.status()).toBe(200);
  return response.json() as Promise<Profile>;
}
async function seed(page: Page, sources: Source[]) {
  const profile = await read(page);
  const response = await page.request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      base_revision: profile.revision,
      data: { ...profile.sections.search, browser_sources: sources },
    },
  });
  expect(response.status(), await response.text()).toBe(200);
}
async function save(page: Page, status = 200) {
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/profile/search") && r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save search", exact: true }).click();
  const saved = await response;
  expect(saved.status()).toBe(status);
  expect(await saved.finished()).toBeNull();
}

test("Search preserves hidden consent, actual source indices, current Off and the ten-source cap", async ({
  browser,
}, info) => {
  const person = invented("o7b-foundation");
  clearAttempts([person.email]);
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page, [[409, /\/api\/profile\/search$/]]);
  const origins = new Set<string>();
  const network: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    origins.add(url.origin);
    if (url.origin !== new URL(caddyUrl ?? "").origin)
      network.push(request.url());
  });
  page.on("requestfailed", (request) =>
    network.push(`${request.url()}: ${request.failure()?.errorText}`),
  );
  page.on("response", (response) => {
    if (response.status() < 400) return;
    const path = new URL(response.url()).pathname;
    if (
      (response.status() === 401 && /^\/api\/auth\/(me|login)$/.test(path)) ||
      (response.status() === 404 && path === "/api/me/model-credential") ||
      (response.status() === 409 && path === "/api/profile/search")
    )
      return;
    network.push(`${response.status()} ${path}`);
  });
  try {
    await signUp(page, person);
    const initial = await page.request.get(platform);
    expect(initial.status()).toBe(200);
    const defaults = (await initial.json()) as {
      enabled: boolean;
      accepted: boolean;
      text_version: number;
    };
    expect(defaults).toMatchObject({ enabled: false, accepted: false });
    const accepted = await page.request.put(platform, {
      headers: CSRF,
      data: {
        enabled: true,
        accept_version: defaults.text_version,
        query: "Robotics engineer",
        location: "Paris",
      },
    });
    expect(accepted.status(), await accepted.text()).toBe(200);
    const protectedSource = (
      await read(page)
    ).sections.search.browser_sources.find((s) => s.kind === "freework");
    expect(protectedSource).toMatchObject({
      query: "Robotics engineer",
      location: "Paris",
      enabled: true,
      text_version: defaults.text_version,
      accepted_at: expect.any(String),
    });
    if (!protectedSource)
      throw new Error("the consent route stored no Free-Work source");
    await seed(page, [
      generic("Alpha robotics"),
      protectedSource,
      generic("Beta robotics"),
    ]);
    await page.goto("/profile?tab=search");
    const form = page.getByRole("form", { name: "Search" });
    const list = form.getByRole("list", { name: "Job sites in your browser" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list.locator('option[value="freework"]')).toHaveCount(0);
    await expect(list.getByRole("switch")).toHaveCount(2);
    await form.getByLabel("Posting age window (days)").fill("21");
    await list
      .getByRole("button", { name: "Move site 2 up", exact: true })
      .click();
    await expect(
      form.getByLabel("Search words of site 1", { exact: true }),
    ).toHaveValue("Beta robotics");
    await save(page);
    expect((await read(page)).sections.search.browser_sources).toEqual([
      generic("Beta robotics"),
      generic("Alpha robotics"),
      protectedSource,
    ]);
    await list
      .getByRole("button", { name: "Move site 1 down", exact: true })
      .click();
    await save(page);
    expect((await read(page)).sections.search.browser_sources).toEqual([
      generic("Alpha robotics"),
      generic("Beta robotics"),
      protectedSource,
    ]);
    await list
      .getByRole("button", { name: "Remove site 1", exact: true })
      .click();
    await save(page);
    await page.reload();
    expect((await read(page)).sections.search).toMatchObject({
      days: 21,
      browser_sources: [generic("Beta robotics"), protectedSource],
    });

    await form.getByLabel("Posting age window (days)").fill("20");
    const off = await page.request.put(platform, {
      headers: CSRF,
      data: { enabled: false },
    });
    expect(off.status()).toBe(200);
    await save(page, 409);
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page
      .getByRole("button", { name: "Keep editing", exact: true })
      .click();
    await save(page);
    const protectedOff = { ...protectedSource, enabled: false };
    expect((await read(page)).sections.search).toMatchObject({
      days: 20,
      browser_sources: [generic("Beta robotics"), protectedOff],
    });
    const freshState = await page.request.get(platform);
    expect(freshState.status()).toBe(200);
    expect(await freshState.json()).toMatchObject({
      enabled: false,
      accepted: true,
      accepted_at: protectedOff.accepted_at,
      accepted_version: protectedOff.text_version,
      query: protectedOff.query,
      location: protectedOff.location,
    });

    const nine = Array.from({ length: 9 }, (_, i) =>
      generic(`Robotics ${i + 1}`),
    );
    await seed(page, [generic("Robotics 1"), protectedOff, ...nine.slice(1)]);
    await page.reload();
    await expect(list.getByRole("listitem")).toHaveCount(9);
    await expect(
      form.getByRole("button", { name: "Add site", exact: true }),
    ).toBeDisabled();
    await list
      .getByRole("button", { name: "Remove site 1", exact: true })
      .click();
    await form.getByRole("button", { name: "Add site", exact: true }).click();
    await form.getByLabel("Site 9", { exact: true }).selectOption("vie");
    await form
      .getByLabel("Search words of site 9", { exact: true })
      .fill("Replacement robotics");
    await form
      .getByLabel("Location (optional) of site 9", { exact: true })
      .fill("Paris");
    await form
      .getByRole("switch", { name: "Read site 9", exact: true })
      .uncheck();
    await save(page);
    await page.reload();
    expect((await read(page)).sections.search.browser_sources).toEqual([
      protectedOff,
      ...nine.slice(1),
      generic("Replacement robotics"),
    ]);
    await expect(list.getByRole("listitem")).toHaveCount(9);
    await expect(
      form.getByRole("button", { name: "Add site", exact: true }),
    ).toBeDisabled();
    await shots(page, "search-protected-consent", false, async () => {
      await form
        .getByRole("heading", {
          name: "Job sites in your browser",
          exact: true,
        })
        .scrollIntoViewIfNeeded();
    });
    expect([...origins]).toEqual([new URL(caddyUrl ?? "").origin]);
    expect(network).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await info.attach("local-network-and-errors", {
      body: JSON.stringify({ origins: [...origins], network, errors }, null, 2),
      contentType: "application/json",
    });
    await context.close();
    clearAttempts([person.email]);
  }
});
