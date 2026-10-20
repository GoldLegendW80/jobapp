import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { deckPosting, readDeck } from "./opportunities-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O2 against the real stack through Caddy plus the fake ATS forms server: the
 * swipe deck. An invented person A has three postings: one on the fake Greenhouse form, one on
 * the fake Ashby form, and a "Cyber­security Engineer" whose soft hyphen the jobs module's whole
 * word match does not see, with the highest fit. A carries the owner's rule against
 * cybersecurity roles (set before the sign up: the invented profile has no never apply rule and
 * the deck adds none of its own), so the card exists and is kept out only by the deck's folded
 * match of that rule: on /discover the cybersecurity card never shows. A Likes the first card (Review first: the toast says the documents are being written),
 * Passes the second with the left arrow, undoes the Pass (the card is back), Passes it again by
 * a mouse drag, reloads (the deck is empty, with the threshold named, and Undo last has nothing
 * to undo). The Like is in the review queue (/applications). Person B, signed in beside A, has
 * an empty deck, and A's posting answers 404 to B's Pass and its undo.
 *
 * Locally, as opportunities-all.config.ts says, then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o2.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const CYBER = "Cyber­security Engineer";
const EMPTY = /Nothing new above your threshold of \d+\./;
const shots = makeShots("opportunities-o2", "o2");

let a: Person;
let b: Person;
let contextA: BrowserContext;
let contextB: BrowserContext;
let pageA: Page;
let pageB: Page;
let errors: string[];
const ids = { first: "", second: "", cyber: "" };
const titles = { first: "", second: "" };

test.beforeAll(async ({ browser }) => {
  a = invented("o2a");
  b = invented("o2b");
  (
    a.profile as { sections: { search: { never_apply: string[] } } }
  ).sections.search.never_apply = [
    "No cybersecurity roles: cyber, security, pentest, threat",
  ];
  clearAttempts([a.email, b.email]);
  contextA = await newContext(browser);
  pageA = await contextA.newPage();
  errors = watchErrors(pageA);
  await signUp(pageA, a);
  titles.first = `${a.posting.title}, Platform`;
  titles.second = `${a.posting.title}, Developer Tools`;
  ids.first = await deckPosting(pageA.request, a, {
    title: titles.first,
    ats: "greenhouse",
    fit: 92,
  });
  ids.second = await deckPosting(pageA.request, a, {
    title: titles.second,
    ats: "ashby",
    fit: 88,
  });
  ids.cyber = await deckPosting(pageA.request, a, {
    title: CYBER,
    ats: "greenhouse",
    fit: 97,
  });
  contextB = await newContext(browser);
  pageB = await contextB.newPage();
  await signUp(pageB, b);
});

test.afterAll(async () => {
  await contextA.close();
  await contextB.close();
});

const card = (page: Page) => page.getByTestId("deck-card");
const heading = (page: Page) => card(page).getByRole("heading", { level: 2 });

test("A swipes: Like, Pass, undo, Pass again; the cybersecurity role never shows", async () => {
  const deck = await readDeck(pageA.request);
  expect(deck.cards.map((c) => c.id)).toEqual([ids.first, ids.second]);

  await pageA.goto("/discover");
  await expect(heading(pageA)).toHaveText(titles.first);
  await expect(pageA.getByTestId("deck-mode")).toHaveText("Review first");
  await expect(pageA.getByText("2 postings left")).toBeVisible();
  await expect(pageA.getByText(/Cyber.?security/)).toHaveCount(0);
  await shots(pageA, "deck");

  await pageA.getByRole("button", { name: "Like", exact: true }).click();
  await expect(
    pageA.getByText("Liked. Your documents are being written."),
  ).toBeVisible();
  await expect(heading(pageA)).toHaveText(titles.second);

  await pageA.keyboard.press("ArrowLeft");
  await expect(pageA.getByText(EMPTY)).toBeVisible();
  await expect(pageA.getByText(/Cyber.?security/)).toHaveCount(0);

  await pageA.getByRole("button", { name: "Undo last" }).click();
  await expect(heading(pageA)).toHaveText(titles.second);

  // A Pass by a mouse drag, well past the threshold.
  const box = await card(pageA).boundingBox();
  if (!box) throw new Error("the card has no box");
  const y = box.y + box.height / 2;
  await pageA.mouse.move(box.x + box.width / 2, y);
  await pageA.mouse.down();
  await pageA.mouse.move(box.x + box.width / 2 - 80, y, { steps: 4 });
  await pageA.mouse.move(box.x + box.width / 2 - 220, y, { steps: 4 });
  await pageA.mouse.up();
  await expect(pageA.getByText(EMPTY)).toBeVisible();
  await shots(pageA, "empty");

  await pageA.reload();
  await expect(pageA.getByText(EMPTY)).toBeVisible();
  await expect(pageA.getByRole("button", { name: "Undo last" })).toBeDisabled();
  await expect(
    pageA.getByRole("link", { name: "Find new postings" }),
  ).toBeVisible();
  expect((await readDeck(pageA.request)).cards).toEqual([]);

  const passed = await pageA.request.get(`/api/jobs/${ids.second}`);
  expect(((await passed.json()) as { status: string }).status).toBe("skipped");
});

test("the Like is in the review queue", async () => {
  await pageA.goto("/applications");
  await expect(pageA.getByText(titles.first).first()).toBeVisible();
  await expect(pageA.getByText(titles.second)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("B sees none of A's cards and cannot swipe them", async () => {
  await pageB.goto("/discover");
  await expect(pageB.getByText(EMPTY)).toBeVisible();
  await expect(card(pageB)).toHaveCount(0);
  expect((await readDeck(pageB.request)).cards).toEqual([]);
  for (const method of ["post", "delete"] as const) {
    const answer = await pageB.request[method](
      `/api/postings/${ids.second}/pass`,
      {
        headers: CSRF,
      },
    );
    expect(answer.status()).toBe(404);
  }
  const like = await pageB.request.post(`/api/postings/${ids.first}/like`, {
    headers: CSRF,
  });
  expect(like.status()).toBe(404);
});
