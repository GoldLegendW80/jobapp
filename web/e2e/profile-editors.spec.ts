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
 * The fact parts of About you, Advanced matching and Settings, Search against the real stack
 * through Caddy, no mocks: a fresh account gets a made up profile through the operator CLI (never
 * Harald's data), then the journey writes a fact part in Markdown and saves it to the profile,
 * moves it up (the fact base keeps the order, owner 2026-11-07), edits the regular expression
 * categories (refused in the browser, then by the server), tries a posting against all the
 * skills, saves and sees the rescore, changes the years in the Skills fields (no model key),
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

/** The profile page, or Settings, Search for the search, which moved there (owner, 2026-11-05). */
async function visit(page: Page, where: "profile" | "search") {
  if (where === "search") {
    await page.goto("/settings/search");
    await expect(page.getByRole("form", { name: "Search" })).toBeVisible();
    return;
  }
  if (!new URL(page.url()).pathname.endsWith("/profile"))
    await page.goto("/profile");
  await expect(
    page.getByRole("form", { name: "Advanced matching" }),
  ).toBeVisible();
}

const part = (page: Page, name: string) =>
  page.getByRole("region", { name, exact: true });
const matching = (page: Page) =>
  page.getByRole("form", { name: "Advanced matching" });

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
  await visit(page, "profile");
  await expect(page.getByTestId("profile-completeness")).toHaveText(
    "100% complete",
  );
});

test("Facts: write a Markdown part, preview it, save it to the profile", async () => {
  await visit(page, "profile");
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
  await visit(page, "profile");
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

test("Advanced matching: add a regular expression, errors from the browser and the server, Try a posting", async () => {
  await visit(page, "profile");
  const form = matching(page);
  // Only the categories written as regular expressions are here; the others are About you's.
  await expect(form.getByRole("list", { name: "Terms of llm" })).toContainText(
    "\\bllms?\\b",
  );
  await expect(form.getByRole("list", { name: "Terms of agents" })).toHaveCount(
    0,
  );
  await form
    .getByRole("button", { name: "Add category to competencies you have" })
    .click();
  await form
    .getByLabel("Name of have category 2", { exact: true })
    .fill("crews");
  await form
    .getByLabel("New term for crews", { exact: true })
    .fill("\\bcrewai\\b");
  await page.keyboard.press("Enter");
  await expect(
    form.getByRole("list", { name: "Terms of crews" }),
  ).toContainText("\\bcrewai\\b");

  // Refused in the browser: nested quantifiers.
  const llm = form.getByLabel("New term for llm", { exact: true });
  await llm.fill("(a+)+");
  await page.keyboard.press("Enter");
  await expect(
    form.getByText(
      "Nested quantifiers such as (a+)+ are not allowed: they can make matching hang.",
    ),
  ).toBeVisible();
  // Accepted by the browser, refused by Python: the server's message lands under the chip.
  await llm.fill("(?<n>a)");
  await page.keyboard.press("Enter");
  await form.getByRole("button", { name: "Save advanced matching" }).click();
  const terms = form.getByRole("list", { name: "Terms of llm" });
  await expect(terms.getByRole("alert")).toContainText(
    /Invalid regular expression at position \d+/,
  );
  await shots(page, "skills-regex-error", false);
  await form.getByRole("button", { name: "Remove (?<n>a)" }).click();

  // Try a posting reads the skills in words of About you and the expressions being edited.
  await page
    .getByLabel("Posting text", { exact: true })
    .fill(
      "We are hiring an Applied AI Engineer to build agents with LangGraph and CrewAI on top of LLMs. Python and FastAPI. PyTorch is a plus. A PhD is not required.",
    );
  const results = page.getByRole("region", { name: "Matches" });
  const have = results.getByRole("list", {
    name: "Competencies you have results",
  });
  await expect(have).toContainText("agentsmatches agents, langgraph");
  await expect(have).toContainText("llmmatches llms");
  await expect(have).toContainText("crewsmatches crewai");
  await expect(
    results.getByRole("list", { name: "Requirements you do not meet results" }),
  ).toContainText("phdmatches phd");
  await expect(
    results.getByRole("list", { name: "Competencies you lack results" }),
  ).toContainText("researchmatches pytorch");
  await shots(page, "skills");
  await form.getByRole("button", { name: "Save advanced matching" }).click();
  await expect(page.getByText("Skills saved.").first()).toBeVisible();
  // Owner, 2026-11-05: the postings are rescored by themselves after a skills change; the
  // worker's tick runs it within seconds and the line beside Save says what changed.
  const rescored = page.getByTestId("rescore-line");
  await expect(rescored).toContainText(
    /more postings? now fits? you at \d+ or more|Rescored: no new posting reaches a fit of \d+/,
    { timeout: 60_000 },
  );
  await expect(rescored).not.toContainText(/[–—]| - /);
  // The categories in words were kept by the save.
  const stored = (await (await page.request.get("/api/profile")).json()) as {
    sections: { skills: { have: { name: string }[] } };
  };
  expect(stored.sections.skills.have.map((one) => one.name).sort()).toEqual([
    "agents",
    "crews",
    "llm",
    "python",
  ]);
  expect(errors).toEqual([]);
});

test("Skills without a model key: the years change by hand, the expressions are kept", async () => {
  await visit(page, "profile");
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
    "crews",
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
  await page.goto("/profile");
  await expect(
    part(page, "POSITIONING").getByRole("heading", {
      name: "Lead with the business",
    }),
  ).toBeVisible();
  const form = matching(page);
  await expect(
    form.getByRole("list", { name: "Terms of crews" }),
  ).toContainText("\\bcrewai\\b");
  await expect(
    form.getByRole("list", { name: "Terms of llm" }),
  ).not.toContainText("(?<n>a)");

  await visit(page, "search");
  await expect(page.getByRole("button", { name: "VIE" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await holdsBoards(page)).toBe(false);
  expect(errors).toEqual([]);
});
