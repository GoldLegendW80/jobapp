import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { readDeck } from "./opportunities-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O7 against the real stack through Caddy: the freelance and project feeds. An
 * invented person A, with no job board, turns on Remote OK and the Hacker News "Freelancer?
 * Seeking freelancer?" thread through the api, starts a sweep, and the worker reads both from the
 * fake feeds server (tests/feeds_stub.py, FEEDS_STUB_BASE_URL of tests/feeds-stub.compose.yml,
 * its `ok` variant: invented companies, never a real platform). A finds the missions in Discover:
 * the card says "via Remote OK" and links back to the posting on Remote OK, as Remote OK asks,
 * and the contract reads "Freelance mission"; the Hacker News client's mission links to its
 * comment. The cybersecurity mission of each feed never shows. Person B, who turned on no feed,
 * has an empty deck and never reads A's mission.
 *
 * Locally, as opportunities-all.config.ts says, with -f tests/feeds-stub.compose.yml added, then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o7.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const shots = makeShots("opportunities-o7", "o7");

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

/** A search with no board: the feeds are the only sources swept. */
function feedsOnly(person: Person) {
  const sections = (
    person.profile as { sections: { search: Record<string, unknown> } }
  ).sections;
  sections.search = {
    ...sections.search,
    title_include: { terms: ["ai engineer", "genai"], advanced_regex: false },
    title_exclude: { terms: [], advanced_regex: false },
    location: { terms: ["remote"], advanced_regex: false },
    contract_types: [],
    min_fit: 0,
    days: 30,
    boards: [],
    never_apply: [
      "No cybersecurity roles: cyber, security, pentest, threat (journey rule)",
    ],
  };
}

const feed = (kind: string) => ({
  kind,
  terms: { terms: [], advanced_regex: false },
  enabled: true,
});

async function turnOnFeeds(request: APIRequestContext, kinds: string[]) {
  const read = await request.get("/api/profile");
  expect(read.status()).toBe(200);
  const profile = (await read.json()) as {
    revision: number;
    sections: { search: Record<string, unknown> };
  };
  const saved = await request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      data: { ...profile.sections.search, feeds: kinds.map(feed) },
      base_revision: profile.revision,
    },
  });
  expect(saved.status(), await saved.text()).toBe(200);
}

async function sweep(request: APIRequestContext) {
  const started = await request.post("/api/jobs/sweeps", { headers: CSRF });
  expect(started.status(), await started.text()).toBe(202);
  const { id } = (await started.json()) as { id: string };
  await expect
    .poll(
      async () => {
        const read = await request.get(`/api/jobs/sweeps/${id}`);
        return ((await read.json()) as { status: string }).status;
      },
      { timeout: 60_000 },
    )
    .toBe("done");
  const read = await request.get(`/api/jobs/sweeps/${id}`);
  return (await read.json()) as {
    sources: Array<{
      kind: string;
      found: number;
      new: number;
      error: string | null;
    }>;
  };
}

async function rows(request: APIRequestContext): Promise<Row[]> {
  const response = await request.get("/api/jobs?limit=100");
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { items: Row[] }).items;
}

async function filterDeck(page: Page, source: string) {
  const advanced = page.getByRole("button", { name: "Advanced" });
  if ((await advanced.getAttribute("aria-expanded")) !== "true")
    await advanced.click();
  const freelance = page.getByRole("checkbox", { name: "Freelance mission" });
  if (!(await freelance.isChecked())) await freelance.check();
  await page.getByLabel("Source").selectOption(source);
}

test.beforeAll(async ({ browser }) => {
  a = invented("o7a");
  b = invented("o7b");
  feedsOnly(a);
  feedsOnly(b);
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

test("A turns on two feeds and the sweep stores their missions", async () => {
  const catalogue = await pageA.request.get("/api/opportunities/feeds");
  expect(catalogue.status()).toBe(200);
  const kinds = (
    (await catalogue.json()) as { supported: Array<{ kind: string }> }
  ).supported.map((s) => s.kind);
  expect(kinds).toEqual(
    expect.arrayContaining(["remoteok", "hackernews_freelance"]),
  );

  await turnOnFeeds(pageA.request, ["remoteok", "hackernews_freelance"]);
  const done = await sweep(pageA.request);
  expect(done.sources.map((s) => [s.kind, s.error])).toEqual([
    ["remoteok", null],
    ["hackernews_freelance", null],
  ]);

  const found = await rows(pageA.request);
  const mission = found.find((r) => r.company === "Quillfeather Example Labs");
  expect(mission).toMatchObject({
    title: "AI Engineer",
    source: "remoteok",
    contract_type: "Freelance",
    status: "found",
  });
  expect(mission?.url).toMatch(/^https:\/\/remoteok\.com\/remote-jobs\//);
  const client = found.find((r) => r.company === "Nimbletide Example");
  expect(client).toMatchObject({
    source: "hackernews:freelance",
    contract_type: "Freelance",
    url: "https://news.ycombinator.com/item?id=4001",
  });
  // Never a cybersecurity mission, in any spelling the feeds use.
  expect(found.filter((r) => /cyber|secu/i.test(r.title))).toEqual([]);
  // Only what the two feeds hold: never We Work Remotely, which A left off.
  expect(found.every((r) => r.source !== "weworkremotely")).toBe(true);
});

test("Discover shows the missions with their source, linking back", async () => {
  await pageA.goto("/discover");
  const card = pageA.getByTestId("deck-card");
  await expect(card).toBeVisible();
  await filterDeck(pageA, "remoteok");
  await expect(card).toContainText("Quillfeather Example Labs");
  await expect(card).toContainText("Freelance mission");
  const link = card.getByRole("link", { name: /via Remote OK/ });
  await expect(link).toHaveAttribute(
    "href",
    /^https:\/\/remoteok\.com\/remote-jobs\//,
  );
  await shots(pageA, "deck-remote-ok");

  // Any source first: the list names the sources of the cards shown, and only Remote OK's are.
  await pageA.getByLabel("Source").selectOption("");
  await expect(
    pageA.getByLabel("Source").locator("option[value='hackernews']"),
  ).toHaveCount(1);
  await pageA.getByLabel("Source").selectOption("hackernews");
  await expect(card).toContainText("Nimbletide Example");
  await expect(card).toContainText("Freelance mission");
  await expect(
    card.getByRole("link", { name: /via Hacker News/ }),
  ).toHaveAttribute("href", "https://news.ycombinator.com/item?id=4001");
  await shots(pageA, "deck-hacker-news");

  const deck = await readDeck(pageA.request);
  expect(deck.cards.some((c) => /cyber|secu/i.test(c.title))).toBe(false);
  expect(errors).toEqual([]);
});

test("B turned on no feed: an empty deck, and A's mission is not B's", async () => {
  const done = await pageB.request.post("/api/jobs/sweeps", { headers: CSRF });
  // No board and no feed: nothing to sweep.
  expect(done.status()).toBe(422);
  expect((await readDeck(pageB.request)).cards).toEqual([]);
  const mission = (await rows(pageA.request)).find(
    (r) => r.company === "Quillfeather Example Labs",
  );
  if (!mission) throw new Error("A's mission was not swept");
  expect((await pageB.request.get(`/api/jobs/${mission.id}`)).status()).toBe(
    404,
  );
});
