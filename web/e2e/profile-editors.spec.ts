import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliImportProfile,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const takeShots = makeShots("profile-p5", "editors");

async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

/**
 * The fact parts of About you, the Skills fields and Settings, Search against the real stack
 * through Caddy, no mocks: a fresh account gets a made up profile through the operator CLI (never
 * Harald's data), then the journey writes a fact part in Markdown and saves it to the profile,
 * moves it up (the fact base keeps the order, owner 2026-11-07), changes the years in the Skills
 * fields (no model key) and keeps the categories stored as regular expressions (Advanced matching
 * is gone, owner 2026-11-08),
 * toggles a contract in Search, reloads and finds every change. Search shows no company boards and the profile holds none
 * (owner, 2026-10-28: the board engine is deleted).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8095 docker compose -p jobapp-p5 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-p5 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-p5 \
 *     pnpm --dir web exec playwright test profile-editors.spec.ts
 *   docker compose -p jobapp-p5 -f compose.yml down -v
 *
 * On the Jetson it runs inside web/e2e/identity-all.config.ts (tests/e2e_jetson.sh): the CLI goes
 * over ssh.
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-editors-${run}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `editors journey ${run} passphrase`;

function fakeExport() {
  const terms = (list: string[], advanced = false) => ({
    advanced_regex: advanced,
    terms: list,
  });
  return {
    format: "jobapp-profile",
    version: 1,
    exported_at: "2026-10-09T08:00:00Z",
    sections: {
      identity: {
        full_name: NAME,
        headline: "Applied AI Engineer",
        email: EMAIL,
        phone: "+33 6 00 00 00 00",
        city: "Lille",
        country: "France",
        linkedin_url: null,
        website: null,
        links: [],
        languages: [{ name: "French", level: "Native" }],
      },
      experience: {
        employers: [
          {
            key: "acme",
            organisation: "Acme Robotics",
            title: "AI Engineer",
            start: "2024-03",
            end: null,
            location: "Lille",
            contract: "CDI",
          },
        ],
        education: [
          {
            school: "École Exemple",
            degree: "MSc",
            start: null,
            end: null,
            location: null,
            details: null,
          },
        ],
      },
      facts: {
        blocks: [
          {
            title: "HARD RULES",
            body: "1. Every claim traces to this base.\n2. No invented metric.",
          },
          {
            title: "WHAT SAM BUILT",
            body: "- A **retrieval** assistant for support agents.\n- An evaluation suite run on every release.",
          },
        ],
      },
      skills: {
        years_experience: 4,
        have: [
          { name: "agents", ...terms(["agent", "langgraph"]) },
          { name: "llm", ...terms(["\\bllms?\\b", "\\bgenai\\b"], true) },
          { name: "python", ...terms(["python", "fastapi"]) },
        ],
        adjacent: [{ name: "mlops", ...terms(["mlflow", "kubeflow"]) }],
        none: [{ name: "research", ...terms(["pytorch", "publications"]) }],
        gates: [{ name: "phd", ...terms(["phd"]), penalty: 25 }],
      },
      search: {
        title_include: terms(["ai engineer", "applied ai"]),
        title_exclude: terms(["intern"]),
        location: terms(["Paris", "Remote"]),
        contract_types: ["CDI"],
        remote_stance: "hybrid",
        min_fit: 70,
        days: 30,
        never_apply: ["No cybersecurity roles."],
        // A job website ticked in Find jobs: what every search runs and what completeness
        // counts (owner, 2026-10-28). An invented host, never opened by this journey.
        interactive_sites: [
          {
            platform: "custom",
            url: "https://jobs.example.org/",
            enabled: true,
            mode: "llm_browser",
          },
        ],
      },
      answers: {
        work_authorization: [
          {
            country: "France",
            answer: "French citizen.",
            needs_sponsorship: false,
          },
        ],
        notice_period: "One month.",
        earliest_start: "January 2027",
        salary_policy: "Against the band.",
        relocation: null,
        travel: null,
        motivation: "I turn prototypes into products.",
        heard_about: null,
        leave_blank: [],
      },
    },
  };
}

function importProfile(email: string) {
  return cliImportProfile(email, fakeExport());
}

/** A tab of the profile page, or Settings, Search for the search, which moved there (owner, 2026-11-05). */
async function visit(page: Page, where: "about" | "search") {
  if (where === "search") {
    await page.goto("/settings/search");
    await expect(page.getByRole("form", { name: "Search" })).toBeVisible();
    return;
  }
  if (new URL(page.url()).pathname !== `/profile/${where}`)
    await page.goto(`/profile/${where}`);
  await expect(
    page.getByRole("heading", { level: 1, name: "Profile" }),
  ).toBeVisible();
}

const part = (page: Page, name: string) =>
  page.getByRole("region", { name, exact: true });

/** The fact blocks the api holds now. */
async function factBlocks(page: Page) {
  const answer = await page.request.get("/api/profile");
  expect(answer.ok()).toBe(true);
  const body = (await answer.json()) as {
    sections: { facts: { blocks: { title: string; body: string }[] } };
  };
  return body.sections.facts.blocks;
}

async function save(page: Page, section: string) {
  await page
    .getByRole("button", { name: `Save ${section.toLowerCase()}` })
    .click();
  await expect(page.getByText(`${section} saved.`).first()).toBeVisible();
}

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  // The server's 422 on a regex Python refuses is part of the journey.
  errors = watchErrors(page, [[422, /\/api\/profile\/skills/]]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("a new account gets a made up profile through the CLI", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  expect(importProfile(EMAIL)).toMatch(/revision/i);
  await visit(page, "about");
  await expect(page.getByTestId("profile-completeness")).toHaveText(
    "100% complete",
  );
});

test("Facts: write a Markdown part, preview it, save it to the profile", async () => {
  await visit(page, "about");
  await expect(part(page, "HARD RULES")).toContainText(
    "Every claim traces to this base.",
  );
  await page.getByRole("button", { name: "Add a part" }).click();
  await part(page, "New part")
    .getByLabel("Heading", { exact: true })
    .fill("POSITIONING");
  const positioning = part(page, "POSITIONING");
  // A level two heading would start a part of its own: the editor writes it as level three.
  await positioning
    .getByLabel("Text", { exact: true })
    .fill(
      "## Lead with the business\n\nHumans set direction, **agents run operations**.\n\n- Sport\n- Casino\n- Poker\n\nSee [the site](https://example.org).",
    );
  const preview = positioning.getByRole("region", { name: "Preview" });
  await expect(
    preview.getByRole("heading", { name: "Lead with the business" }),
  ).toBeVisible();
  await expect(preview.locator("strong")).toHaveText("agents run operations");
  await expect(preview.getByRole("listitem")).toHaveCount(3);
  await expect(preview.getByRole("link", { name: "the site" })).toHaveAttribute(
    "href",
    "https://example.org",
  );
  await shots(page, "facts");

  // On a phone the text and its preview stack, the text first.
  await page.setViewportSize({ width: 375, height: 812 });
  const text = positioning.getByLabel("Text", { exact: true });
  await expect(text).toBeVisible();
  await preview.scrollIntoViewIfNeeded();
  await expect(preview).toBeVisible();
  const [above, below] = await Promise.all([
    text.boundingBox(),
    preview.boundingBox(),
  ]);
  expect((below?.y ?? 0) > (above?.y ?? 0)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 800 });

  // A fact part needs no model: Done saves it to the profile as written.
  await expect(positioning.getByText("Saved", { exact: true })).toBeVisible();
  await positioning.getByRole("button", { name: "Done" }).click();
  await expect(positioning.getByText("Applied to your profile:")).toBeVisible();
  const blocks = await factBlocks(page);
  expect(blocks.map((block) => block.title)).toContain("POSITIONING");
  expect(blocks.find((block) => block.title === "POSITIONING")?.body).toContain(
    "### Lead with the business",
  );
  expect(errors).toEqual([]);
});

test("Facts: a part moves up, the fact base keeps the order, the meter says its size", async () => {
  await visit(page, "about");
  await expect(
    page.getByRole("meter", { name: "Fact base size" }),
  ).toHaveAttribute("aria-valuetext", / of 200 KB$/);
  await page.getByRole("button", { name: "Move POSITIONING up" }).click();
  await expect
    .poll(async () => (await factBlocks(page)).map((block) => block.title))
    .toEqual(["HARD RULES", "POSITIONING", "WHAT SAM BUILT"]);
  await expect(
    page.getByRole("button", { name: "Move POSITIONING up" }),
  ).toBeFocused();
  expect(errors).toEqual([]);
});

test("Skills without a model key: the years change by hand, the expressions are kept", async () => {
  await visit(page, "about");
  const skills = page.getByRole("form", { name: "Skills" });
  await expect(
    skills.getByRole("button", { name: "What I have, agents, python" }),
  ).toBeVisible();
  await skills.getByLabel("Years of experience", { exact: true }).fill("5");
  await save(page, "Skills");
  const stored = (await (await page.request.get("/api/profile")).json()) as {
    sections: {
      skills: { years_experience: number; have: { name: string }[] };
    };
  };
  expect(stored.sections.skills.years_experience).toBe(5);
  expect(stored.sections.skills.have.map((one) => one.name).sort()).toEqual([
    "agents",
    "llm",
    "python",
  ]);
  expect(errors).toEqual([]);
});

test("Search: no boards shown nor held, toggle contracts", async () => {
  await visit(page, "search");
  // Owner, 2026-10-28: searches run the job websites ticked in Find jobs alone, so the tab shows
  // no company boards and the profile holds none.
  await expect(page.getByRole("table", { name: "Boards" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Add company" })).toHaveCount(
    0,
  );
  await expect(
    page.getByText("Searches run the job websites ticked in Find jobs."),
  ).toBeVisible();

  await page.getByRole("button", { name: "VIE" }).click();
  await expect(page.getByRole("button", { name: "VIE" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await shots(page, "search");
  await save(page, "Search");
  expect(await holdsBoards(page)).toBe(false);
  expect(errors).toEqual([]);
});

/** Whether the search section the api returns still names boards or feeds. */
async function holdsBoards(page: Page): Promise<boolean> {
  const answer = await page.request.get("/api/profile");
  expect(answer.ok()).toBe(true);
  const body = (await answer.json()) as {
    sections: { search: Record<string, unknown> };
  };
  return "boards" in body.sections.search || "feeds" in body.sections.search;
}

test("a reload shows every saved change", async () => {
  await page.goto("/profile/about");
  await expect(
    part(page, "POSITIONING").getByRole("heading", {
      name: "Lead with the business",
    }),
  ).toBeVisible();
  await visit(page, "search");
  await expect(page.getByRole("button", { name: "VIE" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await holdsBoards(page)).toBe(false);
  expect(errors).toEqual([]);
});
