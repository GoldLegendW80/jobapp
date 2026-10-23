import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { stub } from "./apply-helpers";
import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { STACK_FORMS, deckPosting, readDeck } from "./opportunities-helpers";
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
    variant: "slow",
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

/** Read real browser rectangles, including fields that could sit under a sticky action. */
async function expectClearDeck(page: Page) {
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBe(0);
  const layout = await page.evaluate(() => {
    const article = document.querySelector('[data-testid="deck-card"]');
    if (!article) throw new Error("the deck card is missing");
    const rectangle = (element: Element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    const content = [
      article,
      ...article.querySelectorAll("h2, p, span, a, button"),
    ].map((element) => ({ text: element.textContent, ...rectangle(element) }));
    const actions = [
      ...document.querySelectorAll("button[aria-keyshortcuts]"),
    ].map((element) => ({
      name: element.getAttribute("aria-keyshortcuts"),
      ...rectangle(element),
    }));
    return { content, actions };
  });
  expect(layout.actions).toHaveLength(3);
  for (const action of layout.actions) {
    expect(action.width, action.name ?? "action").toBeGreaterThanOrEqual(44);
    expect(action.height, action.name ?? "action").toBeGreaterThanOrEqual(44);
    const covered = layout.content.filter(
      (field) =>
        action.x < field.x + field.width &&
        action.x + action.width > field.x &&
        action.y < field.y + field.height &&
        action.y + action.height > field.y,
    );
    if (covered.length > 0) {
      await page.screenshot({
        path: test.info().outputPath("action-overlap.png"),
        animations: "disabled",
      });
    }
    expect(
      covered,
      `action ${action.name} overlaps card content: ${JSON.stringify(layout)}`,
    ).toEqual([]);
  }
}

for (const length of ["short", "long"] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`deck controls leave the ${length} card clear in ${scheme}`, async ({
      browser,
    }, testInfo) => {
      const person = invented(`o2-${length}-${scheme}`);
      clearAttempts([person.email]);
      const context = await newContext(browser);
      try {
        const page = await context.newPage();
        const problems = watchErrors(page);
        page.on("console", (message) => {
          if (message.type() === "warning") problems.push(message.text());
        });
        await signUp(page, person);
        const title =
          length === "short"
            ? "Platform Engineer"
            : "Senior Platform Engineer for developer tools, distributed workflow orchestration and reliable research infrastructure";
        const source = length === "short" ? "remoteok" : "hackernews:freelance";
        const label = length === "short" ? "via Remote OK" : "via Hacker News";
        const url = (await stub.posting("greenhouse")).replace(
          process.env.E2E_ATS_FORMS_URL ?? "",
          STACK_FORMS,
        );
        const imported = await page.request.post("/api/jobs/import", {
          headers: CSRF,
          data: {
            format: "jobapp-jobs",
            version: 1,
            exported_at: "2026-10-01T00:00:00Z",
            warnings: [],
            jobs: [
              {
                slug: `e2e-o2-${length}-${scheme}`,
                title,
                source,
                url,
                company:
                  length === "short"
                    ? "Cedar Example Labs"
                    : "Cedar Example Research Infrastructure and Distributed Developer Tools Cooperative",
                location:
                  length === "short"
                    ? "Remote"
                    : "Nantes, France, with collaboration across Paris, Amsterdam, Helsinki and distributed European research teams",
                contract_type: "CDI",
                status: "found",
                fit: 92,
                found_on: "2026-10-01",
                posting_text: person.posting.posting_text,
                competencies:
                  length === "short"
                    ? ["Python"]
                    : [
                        "Distributed workflow orchestration and reliable service operations",
                        "Developer tooling for reproducible research infrastructure",
                        "Python application design and observability for platform teams",
                      ],
                gaps: ["Kubernetes production capacity planning"],
                pay_min: 70000,
                pay_max: 90000,
                pay_currency: "EUR",
                pay_period: "year",
                work_mode: "remote",
              },
            ],
          },
        });
        expect(imported.ok(), await imported.text()).toBe(true);
        const second = `${person.posting.title}, next card`;
        await deckPosting(page.request, person, {
          title: second,
          ats: "ashby",
          fit: 88,
        });
        const initialIds = (await readDeck(page.request)).cards.map(
          (one) => one.id,
        );
        await page.goto("/discover");
        await expect(heading(page)).toHaveText(title);
        await page.emulateMedia({ colorScheme: scheme });
        const advanced = page.getByRole("button", {
          name: "Advanced",
          exact: true,
        });
        const sourceLink = card(page).getByRole("link", { name: label });
        await expect(sourceLink).toHaveAttribute("href", url);
        await expect(card(page)).toContainText("70,000 to 90,000 EUR a year");
        await expect(card(page)).toContainText("Matches:");
        for (const width of [375, 1280]) {
          await page.setViewportSize({
            width,
            height: width === 375 ? 812 : 800,
          });
          for (const open of [false, true]) {
            if ((await advanced.getAttribute("aria-expanded")) !== String(open))
              await advanced.click();
            await page
              .getByRole("heading", { level: 1, name: "Discover" })
              .scrollIntoViewIfNeeded();
            await expectClearDeck(page);
            await sourceLink.scrollIntoViewIfNeeded();
            await expectClearDeck(page);
            await sourceLink.click({ trial: true });
            expect(
              await sourceLink.evaluate((link) => {
                const box = link.getBoundingClientRect();
                return link.contains(
                  document.elementFromPoint(
                    box.x + box.width / 2,
                    box.y + box.height / 2,
                  ),
                );
              }),
            ).toBe(true);
            await page
              .getByRole("button", { name: "Like", exact: true })
              .scrollIntoViewIfNeeded();
            await expectClearDeck(page);
            await page.screenshot({
              path: testInfo.outputPath(
                `deck-${width}-${open ? "open" : "closed"}.png`,
              ),
              fullPage: true,
              animations: "disabled",
            });
            await expect(card(page)).toHaveAttribute("data-dx", "0");
            await expect(heading(page)).toHaveText(title);
          }
        }
        expect(
          (await readDeck(page.request)).cards.map((one) => one.id),
        ).toEqual(initialIds);
        await page.setViewportSize({ width: 375, height: 812 });
        await page.getByRole("button", { name: "Pass", exact: true }).click();
        await expect(heading(page)).toHaveText(second);
        await page.getByRole("button", { name: "Undo last" }).click();
        await expect(heading(page)).toHaveText(title);
        await page.getByRole("button", { name: "Like", exact: true }).click();
        await expect(heading(page)).toHaveText(second);
        await expect(
          page.getByText("Liked. Your documents are being written."),
        ).toBeVisible();
        expect(problems).toEqual([]);
      } finally {
        await context.close();
        clearAttempts([person.email]);
      }
    });
  }
}
