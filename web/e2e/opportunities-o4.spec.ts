import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { deckPosting, readDeck } from "./opportunities-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O4 against the real stack through Caddy: what the person aims for, and the small
 * bonus it adds to the fit. Invented person A has three postings added by hand before any of it
 * is set: "Senior ..., Payments" whose text names fintech, "..., Build Systems" which names no
 * level and no industry, and "Senior ..., Ledger" (fintech too) whose fit A sets by hand. At phone
 * width (375 px), by keyboard only (focus, Space on the toggles, Enter in the chip input, typing
 * in the fields and the currency select), A selects Senior, opens Advanced, adds fintech and
 * gaming as chips, types a daily rate whose "To" is below its "From": the error shows inline and
 * nothing is sent (the api answers the same body with a 422 naming the field); A fixes it and
 * saves. At desktop width a reload shows every value, the Preferences card sets the tone of
 * documents to Warm with its own Save, a reload keeps it, and the revisions list one per save
 * (search, then preferences). A rescores all postings: the Payments one rises by exactly 7 (two
 * of the three parts: seniority and industry), the Build Systems one keeps its fit and stays in
 * the deck, the Ledger one keeps the fit set by hand, and the Payments card reads "Also fits:
 * senior, fintech.". Person B, signed in beside A, sees none of A's fields. No screen shows a dash.
 *
 * Locally, as opportunities-all.config.ts says, then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o4.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const RATE = "Daily rate (freelance)";
const BELOW = "The maximum is below the minimum.";
const DASH = /[‒–—―]| - /;
const shots = makeShots("opportunities-o4", "o4");

type JobRow = { fit: number | null; fit_overridden: boolean };

let a: Person;
let b: Person;
let contextA: BrowserContext;
let contextB: BrowserContext;
let pageA: Page;
let pageB: Page;
let errors: string[];
const ids = { payments: "", build: "", ledger: "" };
const titles = {
  payments: "Senior Platform Engineer, Payments",
  build: "Platform Engineer, Build Systems",
  ledger: "Senior Platform Engineer, Ledger",
};
const NO_ROW: JobRow = { fit: null, fit_overridden: false };
const before = { payments: NO_ROW, build: NO_ROW, ledger: NO_ROW };

const FINTECH_TEXT =
  "Brindle Example Labs is a fintech company hiring a Senior Platform Engineer.\n\nYou will run our deployment platform on Kubernetes and write backend services in Python.";
// Frontend work the invented profile only has adjacent keeps this fit below the Payments one, so the Payments
// card leads the deck after the rescore.
const PLAIN_TEXT =
  "Brindle Example Labs is hiring a Platform Engineer.\n\nYou will run our deployment platform on Kubernetes and build its dashboard in React and TypeScript.";

/** The person with the posting text replaced, for addPosting and deckPosting. */
const withText = (person: Person, title: string, text: string): Person => ({
  ...person,
  posting: { ...person.posting, title, posting_text: text },
});

async function job(page: Page, id: string): Promise<JobRow> {
  const read = await page.request.get(`/api/jobs/${id}`);
  expect(read.status()).toBe(200);
  return (await read.json()) as JobRow;
}

async function noDash(page: Page, screen: string) {
  const text = await page.evaluate(() => document.body.innerText);
  const hit = DASH.exec(text);
  expect(
    hit ? text.slice(Math.max(0, hit.index - 40), hit.index + 40) : null,
    `a dash on ${screen}`,
  ).toBeNull();
}

const seniority = (page: Page, name: string) =>
  page
    .getByRole("group", { name: "Seniority" })
    .getByRole("button", { name, exact: true });
const rate = (page: Page, which: "From" | "To" | "Currency") =>
  page.getByLabel(`${which} ${RATE}`, { exact: true });
const industries = (page: Page) =>
  page.getByRole("list", { name: "Terms of Industries" });
/** Opens the Advanced of "What you aim for" by keyboard. */
async function openAdvanced(page: Page) {
  const advanced = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "What you aim for" }) })
    .getByRole("button", { name: "Advanced", exact: true });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await advanced.focus();
  await page.keyboard.press("Enter");
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
}
const tone = (page: Page) => page.getByLabel("Tone of documents");

test.beforeAll(async ({ browser }) => {
  a = invented("o4a");
  b = invented("o4b");
  // Below every fit of the three postings, so the deck lists them all, matched or not.
  (
    a.profile as { sections: { search: { min_fit: number } } }
  ).sections.search.min_fit = 40;
  clearAttempts([a.email, b.email]);
  contextA = await newContext(browser);
  pageA = await contextA.newPage();
  errors = watchErrors(pageA);
  await signUp(pageA, a);
  ids.payments = await addPosting(
    pageA.request,
    withText(a, titles.payments, FINTECH_TEXT),
  );
  ids.build = await addPosting(
    pageA.request,
    withText(a, titles.build, PLAIN_TEXT),
  );
  ids.ledger = await deckPosting(
    pageA.request,
    withText(a, titles.ledger, FINTECH_TEXT),
    { title: titles.ledger, ats: null, fit: 66 },
  );
  for (const name of ["payments", "build", "ledger"] as const)
    before[name] = await job(pageA, ids[name]);
  console.log(
    `   fits before: ${Object.entries(before)
      .map(([name, row]) => `${name} ${row.fit}`)
      .join(", ")}`,
  );
  contextB = await newContext(browser);
  pageB = await contextB.newPage();
  await signUp(pageB, b);
});

test.afterAll(async () => {
  await contextA.close();
  await contextB.close();
  clearAttempts([a.email, b.email]);
});

test("A sets seniority, industries and a daily rate by keyboard at 375 px; the 422 shows inline first", async () => {
  expect(before.payments.fit).not.toBeNull();
  expect(before.payments.fit_overridden).toBe(false);
  expect(before.payments.fit!).toBeLessThanOrEqual(93); // room for the 7 points
  expect(before.ledger).toEqual(
    expect.objectContaining({ fit: 66, fit_overridden: true }),
  );

  await pageA.setViewportSize({ width: 375, height: 812 });
  await pageA.goto("/profile?tab=search");
  await expect(seniority(pageA, "Senior")).toHaveAttribute(
    "aria-pressed",
    "false",
  );

  await seniority(pageA, "Senior").focus();
  await pageA.keyboard.press("Space");
  await expect(seniority(pageA, "Senior")).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  await openAdvanced(pageA);
  await pageA.getByLabel("New term for Industries", { exact: true }).focus();
  await pageA.keyboard.type("fintech");
  await pageA.keyboard.press("Enter");
  await expect(industries(pageA)).toContainText("fintech");
  await pageA.keyboard.type("gaming");
  await pageA.keyboard.press("Enter");
  await expect(industries(pageA).getByRole("listitem")).toHaveCount(2);
  await expect(industries(pageA)).toContainText("gaming");

  // From, then Tab to To (below From), then Tab to the currency, chosen by typing its code.
  await rate(pageA, "From").focus();
  await pageA.keyboard.type("500");
  await pageA.keyboard.press("Tab");
  await expect(rate(pageA, "To")).toBeFocused();
  await pageA.keyboard.type("400");
  await pageA.keyboard.press("Tab");
  await expect(rate(pageA, "Currency")).toBeFocused();
  await pageA.keyboard.type("CHF");
  await expect(rate(pageA, "Currency")).toHaveValue("CHF");

  const saves: string[] = [];
  const onRequest = (request: { method: () => string; url: () => string }) => {
    if (request.method() === "PUT" && request.url().includes("/api/profile/"))
      saves.push(request.url());
  };
  pageA.on("request", onRequest);
  const saveSearch = pageA.getByRole("button", { name: "Save search" });
  await saveSearch.focus();
  await pageA.keyboard.press("Enter");
  await expect(pageA.getByText(BELOW)).toBeVisible();
  await expect(rate(pageA, "To")).toHaveAttribute("aria-invalid", "true");
  expect(saves).toEqual([]);
  await noDash(pageA, "the search tab with the error");

  // The api is the judge too: the same section sent as is answers 422 on the daily rate.
  const read = await pageA.request.get("/api/profile");
  const current = (await read.json()) as {
    revision: number;
    sections: { search: Record<string, unknown> };
  };
  const refused = await pageA.request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      base_revision: current.revision,
      data: {
        ...current.sections.search,
        compensation: {
          salary: null,
          daily_rate: { min: 500, max: 400, currency: "CHF" },
        },
      },
    },
  });
  expect(refused.status()).toBe(422);
  const detail = ((await refused.json()) as { detail: string }).detail;
  expect(detail).toContain("compensation.daily_rate");
  expect(detail).toContain("the maximum is below the minimum");

  await rate(pageA, "To").focus();
  await pageA.keyboard.press("ControlOrMeta+a");
  await pageA.keyboard.type("650");
  await saveSearch.focus();
  await pageA.keyboard.press("Enter");
  await expect(pageA.getByText("Search saved.").first()).toBeVisible();
  await expect(pageA.getByText(BELOW)).toHaveCount(0);
  expect(saves).toHaveLength(1);
  pageA.off("request", onRequest);
  await noDash(pageA, "the search tab, saved");
  await shots(pageA, "search-target");
});

test("at desktop width a reload keeps the values; Preferences sets the tone with its own Save", async () => {
  await pageA.setViewportSize({ width: 1280, height: 800 });
  await pageA.reload();
  await expect(seniority(pageA, "Senior")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(seniority(pageA, "Lead")).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await openAdvanced(pageA);
  await expect(industries(pageA).getByRole("listitem")).toHaveCount(2);
  await expect(industries(pageA)).toContainText("fintech");
  await expect(industries(pageA)).toContainText("gaming");
  await expect(rate(pageA, "From")).toHaveValue("500");
  await expect(rate(pageA, "To")).toHaveValue("650");
  await expect(rate(pageA, "Currency")).toHaveValue("CHF");

  await expect(tone(pageA)).not.toHaveValue("warm");
  await tone(pageA).focus();
  await pageA.keyboard.type("Warm");
  await expect(tone(pageA)).toHaveValue("warm");
  await pageA.getByRole("button", { name: "Save preferences" }).focus();
  await pageA.keyboard.press("Enter");
  await expect(pageA.getByText("Preferences saved.").first()).toBeVisible();
  await pageA.reload();
  await expect(tone(pageA)).toHaveValue("warm");
  await expect(seniority(pageA, "Senior")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await noDash(pageA, "the search tab with preferences");

  // One revision per save: the search, then the preferences, newest first.
  const listed = await pageA.request.get("/api/profile/revisions?limit=2");
  const { items } = (await listed.json()) as {
    items: Array<{ revision: number; section: string }>;
  };
  expect(items.map((item) => item.section)).toEqual(["preferences", "search"]);
  const [newest, search] = items;
  expect(newest?.revision).toBe((search?.revision ?? 0) + 1);
  const searches = await pageA.request.get(
    "/api/profile/revisions?section=search",
  );
  // The import, then the one save that went through (the refused one made none).
  expect(((await searches.json()) as { items: unknown[] }).items).toHaveLength(
    2,
  );

  await pageA.getByRole("button", { name: "History of search" }).click();
  const drawer = pageA.getByRole("dialog", { name: "Search history" });
  await expect(drawer).toBeVisible();
  const versions = drawer.getByRole("navigation", {
    name: "Versions of search",
  });
  await expect(versions.getByRole("button")).toHaveCount(2);
  await expect(
    versions.getByRole("button", {
      name: new RegExp(`^Revision ${search?.revision}, current, saved`),
    }),
  ).toBeVisible();
  await pageA.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});

test("a rescore adds 7 to the matching posting only, keeps the hand fit, and the card says why", async () => {
  test.setTimeout(240_000);
  await pageA.goto("/jobs");
  await pageA.getByRole("button", { name: "More actions" }).click();
  await pageA.getByRole("menuitem", { name: "Rescore all postings" }).click();
  const dialog = pageA.getByRole("alertdialog", {
    name: "Rescore all postings?",
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByLabel("Also replace the fits I set by hand"),
  ).not.toBeChecked();
  await dialog.getByRole("button", { name: "Rescore" }).click();
  const summary = pageA.getByText(/^Rescored [\d,]+ postings?: /);
  await expect(summary).toBeVisible({ timeout: 120_000 });
  expect(await summary.textContent()).toContain("1 fit set by hand kept");
  await noDash(pageA, "the jobs list after the rescore");

  const payments = await job(pageA, ids.payments);
  const build = await job(pageA, ids.build);
  const ledger = await job(pageA, ids.ledger);
  expect(payments.fit).toBe(before.payments.fit! + 7);
  expect(payments.fit_overridden).toBe(false);
  expect(build.fit).toBe(before.build.fit);
  expect(ledger).toEqual(
    expect.objectContaining({ fit: 66, fit_overridden: true }),
  );

  const deck = await readDeck(pageA.request);
  const listed = deck.cards.map((card) => card.id);
  expect(listed).toContain(ids.payments);
  expect(listed).toContain(ids.build);
  expect(listed[0]).toBe(ids.payments);

  await pageA.goto("/discover");
  const card = pageA.getByTestId("deck-card");
  await expect(card.getByRole("heading", { level: 2 })).toHaveText(
    titles.payments,
  );
  await expect(card).toContainText("Also fits: senior, fintech.");
  await noDash(pageA, "the deck");
  await shots(pageA, "deck-also-fits");
  expect(errors).toEqual([]);
});

test("B sees none of A's fields", async () => {
  await pageB.goto("/profile?tab=search");
  await expect(seniority(pageB, "Senior")).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await openAdvanced(pageB);
  await expect(industries(pageB).getByRole("listitem")).toHaveCount(0);
  await expect(rate(pageB, "From")).toHaveValue("");
  await expect(rate(pageB, "To")).toHaveValue("");
  await expect(tone(pageB)).not.toHaveValue("warm");
  await noDash(pageB, "B's search tab");

  const read = await pageB.request.get("/api/profile");
  const profile = (await read.json()) as {
    sections: {
      search: {
        seniority: string[];
        industries: { terms: string[] };
        compensation: { daily_rate: unknown };
      };
      preferences: { documents_tone: string } | null;
    };
  };
  expect(profile.sections.search.seniority).toEqual([]);
  expect(profile.sections.search.industries.terms).toEqual([]);
  expect(profile.sections.search.compensation.daily_rate).toBeNull();
  expect(profile.sections.preferences?.documents_tone ?? "plain").not.toBe(
    "warm",
  );
  const deck = await readDeck(pageB.request);
  expect(deck.cards.map((card) => card.id)).not.toContain(ids.payments);
  expect((await pageB.request.get(`/api/jobs/${ids.payments}`)).status()).toBe(
    404,
  );
});
