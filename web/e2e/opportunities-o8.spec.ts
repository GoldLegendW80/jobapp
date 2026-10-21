import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import { caddyUrl, invented, signUp, type Person } from "./documents-helpers";
import { readDeck } from "./opportunities-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O8 against the real stack through Caddy: the freelance and project sources in the
 * profile's Search tab. An invented person A, with no job board, switches on We Work Remotely in
 * the card "Freelance and project sources", saves, reloads and sees it still on; reads why Malt
 * is read only with an opt in (its reason, the date of the check, and no switch); adds a mission
 * by hand from the card (the contract Freelance preselected) and finds it in Discover. A then
 * finds jobs: the sweep reads We Work Remotely from the fake feeds server
 * (tests/feeds-stub.compose.yml, its `ok` variant, invented companies, never a real platform),
 * and its summary lists the feed under its own heading in plain words; the mission's page says
 * "via We Work Remotely" and links back to it there. Person B, who switched on nothing, sees
 * every source off, an empty deck, and never reads A's mission.
 *
 * Locally, as opportunities-all.config.ts says, with -f tests/feeds-stub.compose.yml added, then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o8.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const shots = makeShots("opportunities-o8", "o8");
const DASHES = /[‒–—―]|\s-\s/;

type Row = {
  id: string;
  title: string;
  company: string;
  source: string | null;
  url: string | null;
  contract_type: string;
  status: string;
};

let a: Person;
let b: Person;
let contextA: BrowserContext;
let contextB: BrowserContext;
let pageA: Page;
let pageB: Page;
let errors: string[];

const MISSION = {
  company: "Brackenfold Example Consulting",
  title: "LLM evaluation mission",
};

/** A search with no board and no feed: the person turns sources on in the card. */
function noSources(person: Person) {
  const sections = (
    person.profile as { sections: { search: Record<string, unknown> } }
  ).sections;
  const search = { ...sections.search };
  delete search.feeds;
  sections.search = {
    ...search,
    title_include: {
      terms: ["ai engineer", "forward deployed", "llm"],
      advanced_regex: false,
    },
    title_exclude: { terms: [], advanced_regex: false },
    location: { terms: [], advanced_regex: false },
    contract_types: [],
    min_fit: 0,
    days: 30,
    boards: [],
    never_apply: ["No cybersecurity roles: cyber, security, pentest, threat"],
  };
}

async function rows(request: APIRequestContext): Promise<Row[]> {
  const response = await request.get("/api/jobs?limit=100");
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { items: Row[] }).items;
}

async function sourcesCard(page: Page): Promise<Locator> {
  await page.goto("/profile?tab=search");
  const card = page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Freelance and project sources" }),
    })
    .last();
  await expect(
    card.getByRole("list", { name: "Freelance sources" }),
  ).toBeVisible();
  return card;
}

test.beforeAll(async ({ browser }) => {
  a = invented("o8a");
  b = invented("o8b");
  noSources(a);
  noSources(b);
  clearAttempts([a.email, b.email]);
  contextA = await newContext(browser);
  pageA = await contextA.newPage();
  errors = watchErrors(pageA);
  await signUp(pageA, a);
  contextB = await newContext(browser);
  pageB = await contextB.newPage();
  await signUp(pageB, b);
});

test.afterAll(async () => {
  await contextA.close();
  await contextB.close();
});

test("A switches on We Work Remotely, saves, and it is still on after a reload", async () => {
  const card = await sourcesCard(pageA);
  await expect(
    card.getByText("No freelance source is read.", { exact: false }),
  ).toBeVisible();
  const wwr = card.getByRole("switch", { name: "Read We Work Remotely" });
  await expect(wwr).not.toBeChecked();
  await expect(
    card.getByText("Each posting links back to We Work Remotely."),
  ).toBeVisible();
  // The words are folded under Advanced until opened.
  await expect(card.getByText("Words for We Work Remotely")).toHaveCount(0);
  await wwr.check();
  await pageA.getByRole("button", { name: "Save search" }).click();
  await expect(pageA.getByText("Search saved.")).toBeVisible();

  await pageA.reload();
  const again = await sourcesCard(pageA);
  await expect(
    again.getByRole("switch", { name: "Read We Work Remotely" }),
  ).toBeChecked();
  await expect(
    again.getByRole("switch", { name: "Read Remote OK" }),
  ).not.toBeChecked();
  const profile = (await (await pageA.request.get("/api/profile")).json()) as {
    sections: { search: { feeds?: Array<{ kind: string; enabled: boolean }> } };
  };
  expect(
    profile.sections.search.feeds?.map((f) => [f.kind, f.enabled]),
  ).toEqual([["weworkremotely", true]]);
  await again.getByRole("button", { name: "Advanced" }).click();
  await expect(again.getByText("Words for We Work Remotely")).toBeVisible();
  await again.scrollIntoViewIfNeeded();
  await shots(pageA, "sources-card", false);
});

test("A reads why Malt needs an opt in: its reason, the date, no switch", async () => {
  const card = await sourcesCard(pageA);
  const optIn = card.getByRole("region", {
    name: "Read only with your opt in",
  });
  await expect(optIn).toBeVisible();
  const malt = optIn.getByRole("listitem").filter({ hasText: "Malt" });
  await expect(malt).toContainText(
    "No public mission board: clients contact freelancers in its own messaging.",
  );
  await expect(malt).toContainText("Needs your opt in, coming later");
  await expect(malt).toContainText("Checked on 20 Oct 2026.");
  await expect(optIn.getByRole("switch")).toHaveCount(0);
  await expect(optIn.getByRole("checkbox")).toHaveCount(0);
  for (const text of await optIn.getByRole("listitem").allTextContents())
    expect(text).not.toMatch(DASHES);
  await optIn.scrollIntoViewIfNeeded();
  await shots(pageA, "opt-in-platforms", false);
});

test("A adds a mission by hand and finds it in Discover", async () => {
  const card = await sourcesCard(pageA);
  await card.getByRole("button", { name: "Add a mission by hand" }).click();
  const dialog = pageA.getByRole("dialog", { name: "Add a posting" });
  await expect(dialog.getByLabel("Contract type")).toHaveValue("Freelance");
  await dialog.getByLabel("Company").fill(MISSION.company);
  await dialog.getByLabel("Title").fill(MISSION.title);
  await dialog.getByRole("button", { name: "Add posting" }).click();
  await expect(dialog).toBeHidden();
  // The search form was not saved nor left: the page stays on the Search tab.
  await expect(pageA).toHaveURL(/\/profile\?tab=search$/);
  await card.getByRole("link", { name: "Find it in Discover" }).click();
  await expect(pageA).toHaveURL(/\/discover$/);
  const deckCard = pageA.getByTestId("deck-card");
  await expect(deckCard).toContainText(MISSION.company);
  await expect(deckCard).toContainText("Freelance mission");
  await shots(pageA, "mission-in-discover");
  const added = (await rows(pageA.request)).find(
    (r) => r.company === MISSION.company,
  );
  expect(added).toMatchObject({
    title: MISSION.title,
    contract_type: "Freelance",
    status: "found",
  });
});

test("A finds jobs: the feed is listed apart, in plain words, and links back", async () => {
  await pageA.goto("/jobs");
  await pageA.getByRole("button", { name: "Find jobs" }).click();
  const summary = pageA.getByRole("dialog", { name: /new postings? found/ });
  await expect(summary).toBeVisible({ timeout: 90_000 });
  const feeds = summary.getByRole("region", {
    name: "Freelance and project sources",
  });
  await expect(feeds).toContainText("We Work Remotely:");
  await expect(feeds).toContainText("postings found");
  await expect(feeds).not.toContainText("It stopped early.");
  await expect(
    summary.getByRole("region", { name: "Job sites in your browser" }),
  ).toHaveCount(0);
  expect(await feeds.textContent()).not.toMatch(DASHES);
  await shots(pageA, "sweep-summary", false);
  await pageA.keyboard.press("Escape");

  const found = await rows(pageA.request);
  // Read from the fake feeds server only: every company is invented.
  for (const row of found) expect(row.company).toMatch(/Example/);
  const mission = found.find((r) => r.company === "Tamberlin Example");
  expect(mission).toMatchObject({
    title: "Forward Deployed Engineer",
    source: "weworkremotely",
    contract_type: "Freelance",
  });
  await pageA.goto(`/jobs/${mission!.id}`);
  const via = pageA.getByRole("link", { name: /via We Work Remotely/ });
  await expect(via).toHaveAttribute(
    "href",
    /^https:\/\/weworkremotely\.com\/remote-jobs\//,
  );
  await shots(pageA, "posting-via-feed");

  // Never a cybersecurity mission in the deck.
  const deck = await readDeck(pageA.request);
  expect(deck.cards.length).toBeGreaterThan(0);
  expect(
    deck.cards.some((c) =>
      /cyber|secu|threat/i.test(c.title.replace(/­/g, "")),
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});

test("B switched on nothing: every source off, an empty deck, A's mission not B's", async () => {
  const card = await sourcesCard(pageB);
  for (const name of [
    "Read Remote OK",
    "Read We Work Remotely",
    "Read Hacker News: Freelancer? Seeking freelancer?",
    "Read Hacker News: Who is hiring?",
  ])
    await expect(card.getByRole("switch", { name })).not.toBeChecked();
  expect((await readDeck(pageB.request)).cards).toEqual([]);
  for (const row of await rows(pageA.request))
    expect((await pageB.request.get(`/api/jobs/${row.id}`)).status()).toBe(404);
});
