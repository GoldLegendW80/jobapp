import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import {
  caddyUrl,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("jobs-j6");

/**
 * Jobs J6, the map, against the real stack through Caddy, no mocks: a fresh account receives
 * synthetic postings with real city names over HTTP (POST /api/jobs/import), then the journey
 * opens the map from the list, checks the pins against the api, the tooltip, a place's panel and
 * the way to a detail and back, a status filter that changes the pins, the unplaced list, the
 * keyboard zoom, and the phone layout. Nothing of Harald's is read.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8116 docker compose -p jobapp-j6 -f compose.yml up -d --build --wait
 *   docker compose -p jobapp-j6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8116 E2E_COMPOSE_PROJECT=jobapp-j6 \
 *     pnpm --dir web exec playwright test jobs-map.spec.ts
 *   docker compose -p jobapp-j6 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-jobs-map-${run}@example.org`;
const NAME = "Map Journey";
const PASSWORD = `map journey ${run} passphrase`;
const CSRF = { "X-Requested-With": "jobapp" };

/** [location, status] of every synthetic posting. */
const ROWS: Array<[string, string]> = [
  ["Paris, France", "found"],
  ["Paris, France", "found"],
  ["Paris (hybrid)", "shortlisted"],
  ["La Défense, France", "applied"],
  ["Paris, London", "found"],
  ["London, UK", "applied"],
  ["London, United Kingdom", "skipped"],
  ["Bordeaux, France", "interview"],
  ["Berlin, Germany", "found"],
  ["Zürich, Switzerland", "shortlisted"],
  ["Remote, United States", "found"],
  ["New York, NY", "applied"],
  ["Singapore", "found"],
  ["Sydney, Australia", "skipped"],
  ["Montréal, Canada, VIE 24 months", "found"],
  ["Remote", "found"],
  ["Remote", "shortlisted"],
  ["", "found"],
  ["Atlantis", "found"],
];

function syntheticDocument() {
  return {
    format: "jobapp-jobs",
    version: 1,
    exported_at: new Date().toISOString(),
    jobs: ROWS.map(([location, status], i) => ({
      slug: `e2e-map-${i + 1}`,
      company: `Map Company ${(i % 5) + 1}`,
      title: `Map Engineer ${i + 1}`,
      location,
      url: `https://jobs.example.org/e2e-map/${run}/${i + 1}`,
      status,
      fit: (i * 13) % 100,
      found_on: "2026-10-01",
    })),
    warnings: [],
  };
}

interface PlacesMap {
  places: Array<{ name: string; count: number; job_ids: string[] }>;
  unplaced: Array<{ location: string; count: number }>;
  total: number;
  placed: number;
}

const mapGroup = (page: Page) => page.getByRole("group", { name: "World map" });
const pins = (page: Page) => mapGroup(page).getByRole("button");
const pin = (page: Page, name: string): Locator =>
  mapGroup(page).getByRole("button", { name: new RegExp(`^${name}: `) });

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
});

test("the map of synthetic postings: pins from the api, tooltip, panel, detail and back", async () => {
  test.setTimeout(120_000);
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const imported = await page.request.post("/api/jobs/import", {
    headers: CSRF,
    data: syntheticDocument(),
  });
  expect(imported.ok(), await imported.text()).toBe(true);
  expect(((await imported.json()) as { created: number }).created).toBe(
    ROWS.length,
  );

  // From the list to the map with the tab.
  await page.goto("/jobs");
  await page
    .getByRole("navigation", { name: "Jobs views" })
    .getByRole("link", { name: "Map" })
    .click();
  await expect(page).toHaveURL(/\/jobs\/map$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Jobs map" }),
  ).toBeVisible();

  const api = (await (
    await page.request.get("/api/jobs/places")
  ).json()) as PlacesMap;
  expect(api.total).toBe(ROWS.length);
  // Paris (5 with "Paris, London" and La Défense), London (3), Bordeaux, Berlin, Zurich,
  // United States, New York, Singapore, Sydney, Montreal.
  expect(api.places.map((p) => [p.name, p.count])).toEqual([
    ["Paris", 5],
    ["London", 3],
    ["Berlin", 1],
    ["Bordeaux", 1],
    ["Montreal", 1],
    ["New York", 1],
    ["Singapore", 1],
    ["Sydney", 1],
    ["United States", 1],
    ["Zurich", 1],
  ]);
  expect(api.placed).toBe(15);
  expect(api.unplaced).toEqual([
    { location: "Remote", count: 2 },
    { location: "", count: 1 },
    { location: "Atlantis", count: 1 },
  ]);

  await expect(pins(page)).toHaveCount(api.places.length);
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    `${api.placed} of ${api.total} postings on the map`,
  );
  await expect(pin(page, "Paris")).toHaveAccessibleName(
    "Paris: 5 postings, 3 Found, 1 Shortlisted, 1 Applied",
  );
  // The land is drawn.
  expect(
    (await page.getByTestId("land").getAttribute("d"))?.length ?? 0,
  ).toBeGreaterThan(10_000);

  await pin(page, "London").hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "3 postings, 1 Found, 1 Applied, 1 Skipped",
  );
  await shots(page, "map");

  await pin(page, "Paris").click();
  const panel = page.getByRole("complementary", { name: "Paris" });
  await expect(panel).toBeVisible();
  const links = panel
    .getByRole("list", { name: "Postings in Paris" })
    .getByRole("link");
  await expect(links).toHaveCount(5);
  await shots(page, "panel");

  const first = links.first();
  const href = await first.getAttribute("href");
  await first.click();
  await expect(page).toHaveURL(new RegExp(`${href ?? "none"}$`));
  await page.getByRole("link", { name: "Back to jobs" }).click();
  await expect(page).toHaveURL(/\/jobs\/map$/);
  await expect(pins(page)).toHaveCount(api.places.length);
  expect(errors).toEqual([]);
});

test("a status filter changes the pins and stays in the URL", async () => {
  await page.goto("/jobs/map");
  await expect(pins(page)).toHaveCount(10);
  await page.getByRole("button", { name: /^Status/ }).click();
  await page.getByRole("menuitemcheckbox", { name: "Applied" }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/jobs\/map\?status=applied$/);
  const api = (await (
    await page.request.get("/api/jobs/places?status=applied")
  ).json()) as PlacesMap;
  expect(api.places.map((p) => p.name).sort()).toEqual([
    "London",
    "New York",
    "Paris",
  ]);
  await expect(pins(page)).toHaveCount(3);
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "3 of 3 postings matching on the map",
  );
  await expect(page.getByRole("region", { name: "Unplaced" })).toHaveCount(0);
  await shots(page, "filtered");

  // The list tab keeps the filter.
  await page
    .getByRole("navigation", { name: "Jobs views" })
    .getByRole("link", { name: "List" })
    .click();
  await expect(page).toHaveURL(/\/jobs\?status=applied$/);
  expect(errors).toEqual([]);
});

test("the unplaced locations are listed with their counts", async () => {
  await page.goto("/jobs/map");
  const unplaced = page.getByRole("region", { name: "Unplaced" });
  await expect(unplaced).toContainText("4 postings without a place on the map");
  const items = unplaced
    .getByRole("list", { name: "Unplaced locations" })
    .getByRole("listitem");
  await expect(items).toHaveText([
    "Remote2",
    "Location not stated1",
    "Atlantis1",
  ]);
  expect(errors).toEqual([]);
});

test("the keyboard zooms and pans, the regions frame Europe", async () => {
  await page.goto("/jobs/map");
  const map = mapGroup(page);
  await expect(pins(page)).toHaveCount(10);
  await map.focus();
  await page.keyboard.press("+");
  await expect(map).toHaveAttribute("data-zoom", "1.50");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("-");
  await expect(map).toHaveAttribute("data-zoom", "1.00");
  await page
    .getByRole("group", { name: "Region" })
    .getByRole("button", { name: "Europe" })
    .click();
  await expect(pin(page, "Paris")).toBeVisible();
  await expect(pin(page, "Sydney")).toHaveCount(0);
  // The wheel zooms further in.
  const before = Number(await map.getAttribute("data-zoom"));
  await map.hover();
  await page.mouse.wheel(0, -300);
  await expect
    .poll(async () => Number(await map.getAttribute("data-zoom")))
    .toBeGreaterThan(before);
  await shots(page, "europe", false);
  expect(errors).toEqual([]);
});

test("on a phone the map, the pins and the panel fit in 375 px", async () => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/jobs/map");
  await expect(pins(page)).toHaveCount(10);
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);
  const box = await mapGroup(page).boundingBox();
  expect(box?.width ?? 0).toBeLessThanOrEqual(375);
  expect(box?.width ?? 0).toBeGreaterThan(300);
  // At the world view European pins crowd a phone: frame Europe, then a pin opens its panel
  // below the map.
  await page
    .getByRole("group", { name: "Region" })
    .getByRole("button", { name: "Europe" })
    .click();
  await pin(page, "London").click();
  const panel = page.getByRole("complementary", { name: "London" });
  await expect(panel).toBeVisible();
  await panel.scrollIntoViewIfNeeded();
  await expect(panel.getByRole("link")).toHaveCount(3);
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(errors).toEqual([]);
});
