import { deflateSync, crc32 } from "node:zlib";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  cliImportProfile,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  signIn,
  watchErrors,
} from "./stack";

const takeShots = makeShots("profile-p4", "profile");

/** Screenshots once every toast is gone, so none covers the page. */
async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

/**
 * The profile page against the real stack through Caddy: no mocks. A fresh account gets a made
 * up profile through the operator CLI (never Harald's data), then the journey edits Identity
 * (with a cropped photo), Experience and Answers, reloads, saves the same section from two
 * browsers (409), and checks the unsaved changes guard.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8094 docker compose -p jobapp-p4 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-p4 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8094 E2E_COMPOSE_PROJECT=jobapp-p4 \
 *     pnpm --dir web exec playwright test profile.spec.ts
 *   docker compose -p jobapp-p4 -f compose.yml down -v
 *
 * On the Jetson (E2E_ADMIN_VIA_DEPLOY=1) the profile goes in through the same CLI over ssh, as
 * part of tests/e2e_jetson.sh, which removes the account afterwards.
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-profile-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `profile journey ${run} passphrase`;

/** A made up profile in the export format (api/app/profile/schemas.py). */
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
        city: "Nantes",
        country: "France",
        linkedin_url: "https://www.linkedin.com/in/robin-example/",
        website: "https://robin.example.org/",
        links: [{ label: "github.com/robin", url: "https://github.com/robin" }],
        languages: [
          { name: "French", level: "Native" },
          { name: "English", level: "C1" },
        ],
      },
      experience: {
        employers: [
          {
            key: "acme",
            organisation: "Acme Robotics",
            title: "AI Engineer",
            start: "2024-03",
            end: null,
            location: "Nantes",
            contract: "CDI",
          },
          {
            key: "globex",
            organisation: "Globex",
            title: "Developer (apprenticeship)",
            start: "2021-09",
            end: "2024-02",
            location: "Rennes",
            contract: "apprenticeship",
          },
        ],
        education: [
          {
            school: "École Exemple",
            degree: "MSc Computer Science",
            start: "2019-09",
            end: "2024-06",
            location: "Rennes",
            details: "Specialization: machine learning",
          },
        ],
      },
      facts: {
        blocks: [
          {
            title: "WHAT ROBIN BUILT",
            body: "- A **retrieval** assistant for support agents.\n- An evaluation suite run on every release.\n\nSee [the portfolio](https://robin.example.org/).",
          },
          {
            title: "HOSTILE BLOCK",
            body: '<script>window.__xss = 1</script>\n\n<img src=x onerror="window.__xss = 2">',
          },
        ],
      },
      skills: {
        years_experience: 4,
        have: [
          { name: "agents", ...terms(["agent", "langgraph"]) },
          { name: "llm", ...terms(["\\bllm\\b"], true) },
        ],
        adjacent: [{ name: "mlops", ...terms(["mlflow"]) }],
        none: [],
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
        never_apply: ["No defence roles."],
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
            answer: "French citizen, no sponsorship needed.",
            needs_sponsorship: false,
          },
        ],
        notice_period: "One month.",
        earliest_start: "January 2027",
        salary_policy: "Discuss against the band for the role.",
        relocation: "Open to Paris.",
        travel: "Occasional travel is fine.",
        motivation: "I like turning prototypes into products people use.",
        heard_about: "The careers page.",
        leave_blank: ["Voluntary self identification"],
      },
    },
  };
}

function importProfile(email: string) {
  return cliImportProfile(email, fakeExport());
}

/** A real 600 x 400 PNG (a colour gradient), so the api decodes and re-encodes it. */
function gradientPng(width = 600, height = 400): Buffer {
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      row[1 + x * 3] = Math.round((255 * x) / width);
      row[2 + x * 3] = Math.round((255 * y) / height);
      row[3 + x * 3] = 160;
    }
    rows.push(row);
  }
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.writeUInt8(8, 8); // bit depth
  header.writeUInt8(2, 9); // RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const TABS = ["Identity", "Experience", "Facts", "Skills", "Search", "Answers"];
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
  errors = watchErrors(page, [[409, /\/api\/profile\/identity/]]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("a new account gets a profile through the CLI and sees every tab", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const output = importProfile(EMAIL);
  expect(output).toMatch(/revision/i);

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Profile" }),
  ).toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "Profile completeness" }),
  ).toHaveAttribute("aria-valuenow", "100");
  for (const name of TABS) await expect(tab(page, name)).toBeVisible();

  await expect(page.getByLabel("Full name", { exact: true })).toHaveValue(NAME);
  await expect(page.getByLabel("Language 2", { exact: true })).toHaveValue(
    "English",
  );
  await shots(page, "identity");

  await openTab(page, "Experience");
  await expect(
    page.getByLabel("Organisation of employer 1", { exact: true }),
  ).toHaveValue("Acme Robotics");
  await expect(
    page.getByLabel("Present at employer 1", { exact: true }),
  ).toBeChecked();
  await shots(page, "experience");

  await openTab(page, "Facts");
  await page.getByRole("button", { name: "WHAT ROBIN BUILT, block 1" }).click();
  await expect(
    page
      .getByRole("region", { name: "Preview of block 1" })
      .getByRole("link", { name: "the portfolio" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "HOSTILE BLOCK, block 2" }).click();
  const hostile = page.getByRole("region", { name: "Preview of block 2" });
  await expect(
    hostile.getByText("<script>window.__xss = 1</script>"),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as { __xss?: number }).__xss),
  ).toBeUndefined();
  expect(await hostile.locator("script, img").count()).toBe(0);
  await shots(page, "facts");

  await openTab(page, "Skills");
  await expect(
    page.getByRole("list", { name: "Terms of agents" }),
  ).toContainText("langgraph");
  await shots(page, "skills");

  // Searches run the job websites ticked in Find jobs (owner, 2026-10-28): the tab shows no
  // company boards.
  await openTab(page, "Search");
  await expect(
    page.getByText("Searches run the job websites ticked in Find jobs."),
  ).toBeVisible();
  await expect(page.getByRole("table", { name: "Boards" })).toHaveCount(0);
  await shots(page, "search");

  await openTab(page, "Answers");
  await expect(page.getByLabel("Notice period", { exact: true })).toHaveValue(
    "One month.",
  );
  await shots(page, "answers");
  expect(errors).toEqual([]);
});

test("Identity: edit, crop and upload a photo, save", async () => {
  await openTab(page, "Identity");
  await page
    .getByLabel("Headline", { exact: true })
    .fill("Senior Applied AI Engineer");
  await save(page, "Identity");

  await page.getByLabel("Photo file", { exact: true }).setInputFiles({
    name: "portrait.png",
    mimeType: "image/png",
    buffer: gradientPng(),
  });
  const dialog = page.getByRole("dialog", { name: "Crop your photo" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("400 by 400 pixels")).toBeVisible();
  // Keyboard only: a smaller square, moved to the left.
  const size = dialog.getByLabel("Size", { exact: true });
  await size.focus();
  for (let i = 0; i < 25; i += 1) await page.keyboard.press("ArrowLeft");
  const horizontal = dialog.getByLabel("Horizontal position", { exact: true });
  await horizontal.focus();
  await page.keyboard.press("Home");
  await expect(dialog.getByText("300 by 300 pixels")).toBeVisible();
  await shots(page, "identity-crop", false);
  await dialog.getByRole("button", { name: "Use this photo" }).click();
  await expect(page.getByText("Photo saved.")).toBeVisible();
  await expect(dialog).toHaveCount(0);

  const photo = page.getByRole("img", { name: "Your profile photo" });
  await expect(photo).toBeVisible();
  await expect
    .poll(() => photo.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBe(300);
  const natural = await photo.evaluate((img: HTMLImageElement) => [
    img.naturalWidth,
    img.naturalHeight,
  ]);
  expect(natural).toEqual([300, 300]);
  await expect(photo).toHaveAttribute("src", /\/api\/profile\/photo\?v=/);
  await shots(page, "identity-photo", false);
  expect(errors).toEqual([]);
});

test("Experience: add an employer, reorder, save", async () => {
  await openTab(page, "Experience");
  await page.getByRole("button", { name: "Add employer" }).click();
  await page
    .getByLabel("Organisation of employer 3", { exact: true })
    .fill("Initech Labs");
  await expect(
    page.getByLabel("Key of employer 3", { exact: true }),
  ).toHaveValue("initech-labs");
  await page
    .getByLabel("Job title at employer 3", { exact: true })
    .fill("Intern");
  await page.getByLabel("Start of employer 3", { exact: true }).fill("2021-01");
  await page.getByLabel("End of employer 3", { exact: true }).fill("2021-06");
  await page
    .getByLabel("Contract at employer 3", { exact: true })
    .selectOption("internship");
  // Wrong order first: the end before the start is caught before any request.
  await page.getByLabel("End of employer 3", { exact: true }).fill("2020-06");
  await page.getByRole("button", { name: "Save experience" }).click();
  await expect(page.getByText("The end is before the start.")).toBeVisible();
  await page.getByLabel("End of employer 3", { exact: true }).fill("2021-06");
  await page.getByRole("button", { name: "Move employer 2 down" }).click();
  await expect(
    page.getByLabel("Organisation of employer 2", { exact: true }),
  ).toHaveValue("Initech Labs");
  await save(page, "Experience");
  expect(errors).toEqual([]);
});

test("Answers: edit and save", async () => {
  await openTab(page, "Answers");
  await page
    .getByLabel("Motivation", { exact: true })
    .fill("I ship prototypes that become products.\nThen I measure them.");
  await page.getByRole("button", { name: "Add country" }).click();
  await page
    .getByLabel("Country or region 2", { exact: true })
    .fill("Switzerland");
  await page
    .getByLabel("Answer for country 2", { exact: true })
    .fill("A permit would be needed.");
  await page
    .getByLabel("Needs visa sponsorship in country 2", { exact: true })
    .selectOption("yes");
  await save(page, "Answers");
  expect(errors).toEqual([]);
});

test("a reload shows every saved change", async () => {
  await page.goto("/profile");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(
    "Senior Applied AI Engineer",
  );
  await expect(
    page.getByRole("img", { name: "Your profile photo" }),
  ).toBeVisible();
  await openTab(page, "Experience");
  await expect(
    page.getByLabel("Organisation of employer 1", { exact: true }),
  ).toHaveValue("Acme Robotics");
  await expect(
    page.getByLabel("Organisation of employer 2", { exact: true }),
  ).toHaveValue("Initech Labs");
  await expect(
    page.getByLabel("Organisation of employer 3", { exact: true }),
  ).toHaveValue("Globex");
  await expect(
    page.getByLabel("Contract at employer 2", { exact: true }),
  ).toHaveValue("internship");
  await openTab(page, "Answers");
  await expect(page.getByLabel("Motivation", { exact: true })).toHaveValue(
    "I ship prototypes that become products.\nThen I measure them.",
  );
  await expect(
    page.getByLabel("Needs visa sponsorship in country 2", { exact: true }),
  ).toHaveValue("yes");
  expect(errors).toEqual([]);
});

test("saving the same section from two browsers shows the conflict dialog", async ({
  browser,
}) => {
  const other = await newContext(browser);
  const second = await other.newPage();
  const secondErrors = watchErrors(second);
  await signIn(second, EMAIL, PASSWORD);
  await expect(second).toHaveURL(/\/jobs$/);
  await second.goto("/profile?tab=identity");
  await page.goto("/profile?tab=identity");
  await expect(second.getByLabel("City", { exact: true })).toHaveValue(
    "Nantes",
  );

  await page.getByLabel("City", { exact: true }).fill("Paris");
  await save(page, "Identity");

  await second.getByLabel("City", { exact: true }).fill("Lyon");
  await second.getByRole("button", { name: "Save identity" }).click();
  const dialog = second.getByRole("alertdialog", {
    name: "This section changed elsewhere",
  });
  await expect(dialog).toBeVisible();
  await shots(second, "identity-conflict", false);

  // Keep editing, then save again: mine replaces the other version.
  await dialog.getByRole("button", { name: "Keep editing" }).click();
  await expect(second.getByLabel("City", { exact: true })).toHaveValue("Lyon");
  await save(second, "Identity");

  // The first browser is now the stale one: Reload takes the current version.
  await page.getByLabel("Headline", { exact: true }).fill("Stale edit");
  await page.getByRole("button", { name: "Save identity" }).click();
  const stale = page.getByRole("alertdialog", {
    name: "This section changed elsewhere",
  });
  await expect(stale).toBeVisible();
  await stale.getByRole("button", { name: "Reload (discard mine)" }).click();
  await expect(page.getByLabel("City", { exact: true })).toHaveValue("Lyon");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(
    "Senior Applied AI Engineer",
  );
  // The secondary browser only ever saw the 409 it was expected to see.
  expect(secondErrors.filter((e) => !/status of 409/.test(e))).toEqual([]);
  await other.close();
  expect(errors).toEqual([]);
});

test("unsaved changes: switching tab and leaving the page ask first", async () => {
  await page.goto("/profile?tab=identity");
  await page.getByLabel("Headline", { exact: true }).fill("Not saved yet");
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  await tab(page, "Experience").click();
  const guard = page.getByRole("alertdialog", {
    name: "Discard unsaved changes?",
  });
  await expect(guard).toBeVisible();
  await shots(page, "unsaved-guard", false);
  await guard.getByRole("button", { name: "Keep editing" }).click();
  await expect(tab(page, "Identity")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(
    "Not saved yet",
  );

  await page.getByRole("link", { name: "jobapp" }).click();
  await expect(guard).toBeVisible();
  await guard.getByRole("button", { name: "Keep editing" }).click();
  await expect(page).toHaveURL(/\/profile\?tab=identity$/);

  // Closing the tab asks the browser's own question.
  const closing = await context.newPage();
  await closing.goto("/profile?tab=identity");
  await closing.getByLabel("Headline", { exact: true }).fill("Also not saved");
  const asked = new Promise<string>((resolve) => {
    closing.once("dialog", (d) => {
      resolve(d.type());
      void d.accept();
    });
  });
  await closing.close({ runBeforeUnload: true });
  expect(await asked).toBe("beforeunload");

  await page.getByRole("link", { name: "jobapp" }).click();
  await guard.getByRole("button", { name: "Discard changes" }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await page.goto("/profile");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(
    "Senior Applied AI Engineer",
  );
  expect(errors).toEqual([]);
});
