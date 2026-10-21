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
 * Opportunities O3 against the real stack through Caddy: the pay and the work mode of a
 * posting. An invented person A sweeps the board "fake-pay" of the fake ATS server
 * (api/tests/jobs/fake_ats.py, which the worker reads for every org on the scratch stack) on
 * Ashby (Lever's salaryRange is the api tests' part: the same addresses on two boards are one
 * row since browser B10): one posting whose ATS states its pay and work mode, a freelance
 * mission whose text states "TJM 600 €" and whose location says "Télétravail", one that states
 * no pay ("Competitive salary, selon profil"). The postings carry exactly what was stated, never
 * a guess; the deck's cards and the posting's page show it; A edits the pay and the work mode
 * by hand, and a rescore and a new sweep keep A's values. Person B never reads A's posting.
 *
 * Locally, as opportunities-all.config.ts says, then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o3.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const shots = makeShots("opportunities-o3", "o3");
const STATED = "AI Engineer, Pay Stated";
const FREELANCE = "GenAI Engineer, Freelance";
const UNSTATED = "AI Engineer, Pay Not Stated";

type Row = {
  id: string;
  title: string;
  source: string | null;
  pay_min: number | null;
  pay_max: number | null;
  pay_currency: string | null;
  pay_period: string | null;
  work_mode: string | null;
};

let a: Person;
let b: Person;
let contextA: BrowserContext;
let contextB: BrowserContext;
let pageA: Page;
let pageB: Page;
let errors: string[];

const board = (ats: string) => ({
  company: `Pay Example ${ats}`,
  ats,
  org: "fake-pay",
  enabled: true,
  extra_exclude: { terms: [], advanced_regex: false },
});

function sweepsFakePay(person: Person) {
  const sections = (
    person.profile as { sections: { search: Record<string, unknown> } }
  ).sections;
  sections.search = {
    ...sections.search,
    title_include: { terms: ["ai engineer", "genai"], advanced_regex: false },
    location: { terms: ["paris"], advanced_regex: false },
    contract_types: [],
    min_fit: 0,
    boards: [board("ashby")],
  };
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
}

async function rows(request: APIRequestContext): Promise<Row[]> {
  const response = await request.get("/api/jobs?limit=100");
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { items: Row[] }).items;
}

const pay = (row: Row | undefined) =>
  row
    ? [
        row.pay_min,
        row.pay_max,
        row.pay_currency,
        row.pay_period,
        row.work_mode,
      ]
    : [];
const fact = (page: Page, label: string) =>
  page.locator("dt", { hasText: label }).locator("xpath=following-sibling::dd");

test.beforeAll(async ({ browser }) => {
  a = invented("o3a");
  b = invented("o3b");
  sweepsFakePay(a);
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

test("the sweep stores the pay and work mode each posting states, never a guess", async () => {
  await sweep(pageA.request);
  const found = await rows(pageA.request);
  const of = (title: string) => found.find((r) => r.title === title);
  expect(found).toHaveLength(3);
  // Ashby's compensation and workplaceType.
  expect(pay(of(STATED))).toEqual([60000, 70000, "EUR", "year", "hybrid"]);
  // No pay from the ATS: the text's daily rate, the location's "Télétravail".
  expect(pay(of(FREELANCE))).toEqual([600, 600, "EUR", "day", "remote"]);
  // "Competitive salary, selon profil": nothing.
  expect(pay(of(UNSTATED))).toEqual([null, null, null, null, null]);

  const deck = (await readDeck(pageA.request)) as unknown as {
    cards: Array<{ id: string; pay: string | null; work_mode: string | null }>;
  };
  const card = (row: Row | undefined) =>
    deck.cards.find((c) => c.id === row?.id);
  expect(card(of(STATED))?.pay).toBe("60,000 to 70,000 EUR a year");
  expect(card(of(FREELANCE))).toMatchObject({
    pay: "600 EUR a day",
    work_mode: "remote",
  });
  expect(card(of(UNSTATED))).toMatchObject({
    pay: null,
    work_mode: null,
  });

  // The first card on /discover shows its pay as the api states it.
  await pageA.goto("/discover");
  const first = deck.cards[0];
  const shown = pageA.getByTestId("deck-card");
  await expect(shown).toBeVisible();
  if (first?.pay) await expect(shown).toContainText(first.pay);
  else await expect(shown).not.toContainText(/ EUR a | GBP a /);
  await shots(pageA, "deck-card-pay");
});

test("the posting's page shows them; A's edit stays through a rescore and a sweep", async () => {
  const row = (await rows(pageA.request)).find((r) => r.title === STATED);
  if (!row) throw new Error("the posting was not swept");
  await pageA.goto(`/jobs/${row.id}`);
  await expect(fact(pageA, "Pay")).toHaveText("60,000 to 70,000 EUR a year");
  await expect(fact(pageA, "Work mode")).toHaveText("Hybrid");
  await shots(pageA, "posting-pay");

  await pageA.getByRole("button", { name: "Edit details" }).click();
  const dialog = pageA.getByRole("dialog", { name: "Edit details" });
  await dialog.getByLabel("Work mode").selectOption("remote");
  await dialog.getByLabel("Lowest pay").fill("65000");
  await dialog.getByLabel("Highest pay").fill("75000");
  await shots(pageA, "edit-pay", false);
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toBeHidden();
  await expect(fact(pageA, "Pay")).toHaveText("65,000 to 75,000 EUR a year");
  await expect(fact(pageA, "Work mode")).toHaveText("Remote");
  await expect(pageA.getByText("Edited the pay, work mode")).toBeVisible();

  const rescored = await pageA.request.post("/api/jobs/rescore", {
    headers: CSRF,
    data: {},
  });
  expect(rescored.ok(), await rescored.text()).toBe(true);
  await sweep(pageA.request);
  await pageA.reload();
  await expect(fact(pageA, "Pay")).toHaveText("65,000 to 75,000 EUR a year");
  await expect(fact(pageA, "Work mode")).toHaveText("Remote");
  const after = (await rows(pageA.request)).filter((r) => r.title === STATED);
  expect(after).toHaveLength(1); // the sweep added nothing twice
  expect(errors).toEqual([]);

  // B never reads A's posting, and B's deck holds nothing of A's.
  expect((await pageB.request.get(`/api/jobs/${row.id}`)).status()).toBe(404);
  const patched = await pageB.request.patch(`/api/jobs/${row.id}`, {
    headers: CSRF,
    data: { work_mode: "office" },
  });
  expect(patched.status()).toBe(404);
  expect((await readDeck(pageB.request)).cards).toEqual([]);
});
