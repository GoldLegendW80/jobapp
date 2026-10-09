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
 * The Facts, Skills and Search editors against the real stack through Caddy, no mocks: a fresh
 * account gets a made up profile through the operator CLI (32 fake boards, never Harald's data),
 * then the journey edits each tab, saves, reloads and finds every change. The board check calls
 * the real ATS APIs from the api container: one real Ashby board (mistral.ai) and one that does
 * not exist.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8095 docker compose -p jobapp-p5 -f compose.yml up -d --build --wait
 *   docker compose -p jobapp-p5 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-p5 \
 *     pnpm --dir web exec playwright test profile-editors.spec.ts
 *   docker compose -p jobapp-p5 -f compose.yml down -v
 *
 * On the Jetson it runs inside web/e2e/identity-all.config.ts (tests/e2e_jetson.sh): the CLI goes
 * over ssh and the board check calls the ATS from the Jetson's api container.
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-editors-${run}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `editors journey ${run} passphrase`;
const REAL_ORG = "mistral.ai";
const MISSING_ORG = `jobapp-no-such-board-${run}`;

const FAKE_COMPANIES = [
  "Zephyr Labs",
  "Acme Robotics",
  "Northwind AI",
  "Globex",
  "Initech",
  "Umbrella Data",
  "Hooli",
  "Vandelay Systems",
  "Stark Analytics",
  "Wayne Compute",
  "Tyrell Models",
  "Cyberdyne Tools",
  "Soylent Search",
  "Wonka Vision",
  "Gringotts Ledger",
  "Oscorp Agents",
  "Aperture Eval",
  "Black Mesa Ops",
  "Monarch Retail",
  "Pied Piper",
  "Massive Dynamic",
  "Nakatomi Cloud",
  "Duff Insights",
  "Prestige Worldwide",
  "Sirius Cyber",
  "Bluth Logistics",
  "Dunder Paper AI",
  "Kramerica",
  "Los Pollos Data",
  "Rekall Memory",
  "Spacely Sprockets",
  "Weyland Yutani",
];

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
        boards: FAKE_COMPANIES.map((company, index) => ({
          company,
          ats: (["ashby", "greenhouse", "lever"] as const)[index % 3],
          org: company.toLowerCase().replaceAll(" ", "-"),
          enabled: index % 5 !== 0,
          extra_exclude: terms(index === 1 ? ["sales"] : []),
        })),
        never_apply: ["No cybersecurity roles."],
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

const tab = (page: Page, name: string) =>
  page.getByRole("tab", { name: new RegExp(`^${name}, \\d+% complete$`) });

async function openTab(page: Page, name: string) {
  await tab(page, name).click();
  await expect(tab(page, name)).toHaveAttribute("aria-selected", "true");
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
  await page.goto("/profile?tab=facts");
  await expect(
    page.getByRole("progressbar", { name: "Profile completeness" }),
  ).toHaveAttribute("aria-valuenow", "100");
});

test("Facts: add a Markdown block, preview it, save", async () => {
  await openTab(page, "Facts");
  await expect(
    page.getByRole("button", { name: "HARD RULES, block 1" }),
  ).toHaveAttribute("aria-expanded", "false");
  const meter = page.getByRole("meter", { name: "Fact base size" });
  const before = Number(await meter.getAttribute("aria-valuenow"));

  await page.getByRole("button", { name: "Add block" }).click();
  const title = page.getByLabel("Title of block 3", { exact: true });
  await expect(title).toBeFocused();
  await title.fill("POSITIONING");
  await page
    .getByLabel("Text of block 3", { exact: true })
    .fill(
      "## Lead with the business\n\nHumans set direction, **agents run operations**.\n\n- Sport\n- Casino\n- Poker\n\nSee [the site](https://example.org).",
    );
  const preview = page.getByRole("region", { name: "Preview of block 3" });
  await expect(
    preview.getByRole("heading", { name: "Lead with the business" }),
  ).toBeVisible();
  await expect(preview.locator("strong")).toHaveText("agents run operations");
  await expect(preview.getByRole("listitem")).toHaveCount(3);
  await expect(preview.getByRole("link", { name: "the site" })).toHaveAttribute(
    "href",
    "https://example.org",
  );
  expect(Number(await meter.getAttribute("aria-valuenow"))).toBeGreaterThan(
    before,
  );
  await page.getByRole("button", { name: "WHAT SAM BUILT, block 2" }).click();
  await shots(page, "facts");

  // On a phone the editor and the preview take turns.
  await page.setViewportSize({ width: 375, height: 812 });
  const views = page.getByRole("group", { name: "Editor view of block 3" });
  await expect(
    page.getByLabel("Text of block 3", { exact: true }),
  ).toBeVisible();
  await expect(preview).toBeHidden();
  await views.getByRole("button", { name: "Preview" }).click();
  await expect(preview).toBeVisible();
  await expect(
    page.getByLabel("Text of block 3", { exact: true }),
  ).toBeHidden();
  await takeShots(page, "facts-mobile-preview", false);
  // takeShots ends at desktop size, where both panes show again.
  await expect(
    page.getByLabel("Text of block 3", { exact: true }),
  ).toBeVisible();

  await save(page, "Facts");
  expect(errors).toEqual([]);
});

test("Skills: add a term, regex errors from the browser and the server, Try it", async () => {
  await openTab(page, "Skills");
  await page.getByLabel("New term for agents", { exact: true }).fill("crewai");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("list", { name: "Terms of agents" }),
  ).toContainText("crewai");

  // Refused in the browser: nested quantifiers.
  const llm = page.getByLabel("New term for llm", { exact: true });
  await llm.fill("(a+)+");
  await page.keyboard.press("Enter");
  await expect(
    page.getByText(
      "Nested quantifiers such as (a+)+ are not allowed: they can make matching hang.",
    ),
  ).toBeVisible();
  // Accepted by the browser, refused by Python: the server's message lands under the chip.
  await llm.fill("\\1(a)");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save skills" }).click();
  const terms = page.getByRole("list", { name: "Terms of llm" });
  await expect(terms.getByRole("alert")).toContainText(
    /Invalid regular expression at position \d+/,
  );
  await shots(page, "skills-regex-error", false);
  await page.getByRole("button", { name: "Remove \\1(a)" }).click();

  await page
    .getByLabel("Posting text", { exact: true })
    .fill(
      "We are hiring an Applied AI Engineer to build agents with LangGraph and CrewAI on top of LLMs. Python and FastAPI. PyTorch is a plus. A PhD is not required.",
    );
  const results = page.getByRole("region", { name: "Matches" });
  await expect(
    results.getByRole("list", { name: "Competencies you have results" }),
  ).toContainText("agentsmatches agents, langgraph, crewai");
  await expect(
    results.getByRole("list", { name: "Competencies you have results" }),
  ).toContainText("llmmatches llms");
  await expect(
    results.getByRole("list", { name: "Gates results" }),
  ).toContainText("phdmatches phd");
  await expect(
    results.getByRole("list", { name: "Competencies you lack results" }),
  ).toContainText("researchmatches pytorch");
  await shots(page, "skills");
  await save(page, "Skills");
  expect(errors).toEqual([]);
});

test("Search: check a real board, refuse a missing one, add it, toggle contracts", async () => {
  await openTab(page, "Search");
  await expect(page.getByText("32 boards, 25 enabled")).toBeVisible();

  // Sorted by company and searched.
  const header = page.getByRole("columnheader", { name: /Company/ });
  await header.getByRole("button").click();
  await expect(header).toHaveAttribute("aria-sort", "ascending");
  await expect(
    page.getByRole("table", { name: "Boards" }).getByRole("textbox").first(),
  ).toHaveValue("Acme Robotics");
  await page.getByRole("searchbox", { name: "Search boards" }).fill("lever");
  await expect(page.getByText("10 of 32 boards shown")).toBeVisible();
  await page.getByRole("searchbox", { name: "Search boards" }).fill("");

  const status = page
    .getByRole("region", { name: "Add company" })
    .getByRole("status");
  await page.getByLabel("New board ATS", { exact: true }).selectOption("ashby");
  await page.getByLabel("New board org", { exact: true }).fill(MISSING_ORG);
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await expect(status).toContainText(
    `No board named “${MISSING_ORG}” on Ashby`,
    {
      timeout: 20_000,
    },
  );
  await expect(
    page.getByRole("button", { name: /^Add (board|without a check)$/ }),
  ).toHaveCount(0);

  await page
    .getByLabel("New board company", { exact: true })
    .fill("Mistral AI");
  await page.getByLabel("New board org", { exact: true }).fill(REAL_ORG);
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await expect(status).toContainText(/^Found on Ashby: \d+ open postings?\.$/, {
    timeout: 20_000,
  });
  const found = await status.textContent();
  expect(Number(/(\d+) open/.exec(found ?? "")?.[1])).toBeGreaterThan(0);
  await shots(page, "search-check-found", false);
  await page.getByRole("button", { name: "Add board" }).click();
  await expect(page.getByText("33 boards, 26 enabled")).toBeVisible();
  await expect(
    page.getByRole("switch", { name: "Sweep Mistral AI" }),
  ).toBeChecked();

  await page.getByRole("button", { name: "VIE" }).click();
  await expect(page.getByRole("button", { name: "VIE" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page
    .getByRole("button", { name: "Exclude terms of Acme Robotics" })
    .click();
  await expect(
    page.getByRole("list", { name: "Terms of Exclude terms of Acme Robotics" }),
  ).toContainText("sales");
  await shots(page, "search");
  await save(page, "Search");
  expect(errors).toEqual([]);
});

test("a reload shows every saved change", async () => {
  await page.goto("/profile?tab=facts");
  await page.getByRole("button", { name: "POSITIONING, block 3" }).click();
  await expect(
    page
      .getByRole("region", { name: "Preview of block 3" })
      .getByRole("heading", { name: "Lead with the business" }),
  ).toBeVisible();

  await openTab(page, "Skills");
  await expect(
    page.getByRole("list", { name: "Terms of agents" }),
  ).toContainText("crewai");
  await expect(
    page.getByRole("list", { name: "Terms of llm" }),
  ).not.toContainText("\\1(a)");

  await openTab(page, "Search");
  await expect(page.getByLabel("Org of board 33", { exact: true })).toHaveValue(
    REAL_ORG,
  );
  await expect(
    page.getByLabel("Company of board 33", { exact: true }),
  ).toHaveValue("Mistral AI");
  await expect(page.getByRole("button", { name: "VIE" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText("33 boards, 26 enabled")).toBeVisible();
  expect(errors).toEqual([]);
});
