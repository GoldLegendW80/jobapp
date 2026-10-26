import { expect, test, type Page } from "@playwright/test";
import { stub } from "./apply-helpers";
import { CSRF, caddyUrl, invented, keep, signUp } from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";
type ProfileRun = {
  id: string;
  state: string;
  needs: string | null;
  sections: {
    section: string;
    label: string;
    state: string;
    values_hash: string;
    error_code: string | null;
    fields: { label: string; value: unknown; replace: boolean }[];
  }[];
};

test.describe.configure({ timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the dedicated stack running");
const SITE = "http://atsforms:8080/forms/a18/";
const shots = makeShots("apply-a18", "a18");
async function read(page: Page, id: string): Promise<ProfileRun> {
  const response = await page.request.get(`/api/site-profiles/${id}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as ProfileRun;
}
async function start(page: Page, file: string) {
  const response = await page.request.post("/api/site-profiles", {
    headers: CSRF,
    data: { url: SITE + file },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as ProfileRun;
}
async function signIn(page: Page) {
  const banner = page.getByRole("region", {
    name: "Sign in to atsforms",
    exact: true,
  });
  await expect(banner).toBeVisible({ timeout: 180_000 });
  const open = banner.getByRole("button", { name: /Open the live view/ });
  if (await open.count()) await open.click();
  await expect
    .poll(
      async () =>
        banner.getByTestId("live-screen").evaluate((screen) => {
          const canvas = screen.querySelector("canvas");
          const context = canvas?.getContext("2d");
          if (!canvas || !context || !canvas.width) return false;
          const { data } = context.getImageData(
            0,
            0,
            canvas.width,
            canvas.height,
          );
          for (let index = 4; index < data.length; index += 4 * 97)
            if (
              data[index] !== data[0] ||
              data[index + 1] !== data[1] ||
              data[index + 2] !== data[2]
            )
              return true;
          return false;
        }),
      { timeout: 60000 },
    )
    .toBe(true);
  await banner.getByRole("button", { name: "Keyboard", exact: true }).click();
  const keyboard = banner.getByLabel("Type into your browser");
  await keyboard.pressSequentially("fixture-word");
  const pixels = () =>
    banner.getByTestId("live-screen").evaluate((screen) => {
      const canvas = screen.querySelector("canvas")!;
      const bytes = canvas
        .getContext("2d")!
        .getImageData(0, 0, canvas.width, canvas.height).data;
      const samples: number[] = [];
      for (let index = 0; index < bytes.length; index += 4 * 97)
        samples.push(
          bytes[index]! + bytes[index + 1]! * 256 + bytes[index + 2]! * 65536,
        );
      return samples;
    });
  const before = await pixels();
  await keyboard.press("Enter");
  await expect
    .poll(
      async () => {
        const after = await pixels();
        return after.filter((value, index) => value !== before[index]).length;
      },
      { timeout: 60000 },
    )
    .toBeGreaterThan(20);
  await banner
    .getByRole("button", { name: "I'm signed in", exact: true })
    .click();
}

test("Account starts a signed-in profile review, selected Replace and a user edit save once", async ({
  browser,
}) => {
  const person = invented("a18-review");
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await stub.reset();
    await signUp(page, person);
    await page.goto("/account");
    await page
      .getByLabel("Site address", { exact: true })
      .fill(SITE + "portal.html?variant=signin");
    await page
      .getByRole("button", {
        name: "Complete my profile on this site",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/site-profiles/);
    const id = page.url().split("/").pop()!;
    await expect
      .poll(
        async () => {
          const run = await read(page, id);
          return run.state === "ready" || run.needs === "signin";
        },
        { timeout: 180000 },
      )
      .toBe(true);
    if ((await read(page, id)).needs === "signin") await signIn(page);
    await expect
      .poll(async () => (await read(page, id)).state, { timeout: 180000 })
      .toBe("ready");
    expect(await stub.submissions()).toEqual([]);
    const run = await read(page, id);
    const part = run.sections[0]!;
    const name = part.fields.find((field) => field.label === "Full name")!;
    const edited = String(name.value).toUpperCase();
    const card = page.locator(
      `[aria-labelledby="profile-section-${part.section}"]`,
    );
    await card
      .getByRole("textbox", { name: "Edit Full name", exact: true })
      .fill(edited);
    await card
      .getByRole("row", { name: /Full name/ })
      .getByRole("button", { name: "Keep edit" })
      .click();
    await card
      .getByRole("checkbox", { name: "Replace Job title", exact: true })
      .check();
    await expect
      .poll(async () => {
        const current = await read(page, id);
        return current.sections[0]!.fields.find(
          (field) => field.label === "Job title",
        )!.replace;
      })
      .toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      .toBe(true);
    keep(
      "apply-a18",
      "mobile-review.png",
      await page.screenshot({ fullPage: true }),
    );
    await shots(page, "section-review");
    await card
      .getByRole("button", { name: "Approve section", exact: true })
      .click();
    await expect(page.getByRole("alertdialog")).toContainText("atsforms");
    await page
      .getByRole("button", { name: "Approve and save section", exact: true })
      .click();
    await expect
      .poll(async () => (await read(page, id)).sections[0]!.state, {
        timeout: 180000,
      })
      .toBe("saved");
    const saved = await stub.submissions();
    expect(saved).toHaveLength(1);
    expect(saved[0]!.fields.name).toBe(edited);
    expect(saved[0]!.fields.title).toBe(
      part.fields.find((field) => field.label === "Job title")!.value,
    );
    expect(saved[0]!.fields.team).toBe("");
    const again = await page.request.post(
      `/api/site-profiles/${id}/sections/${part.section}/approve`,
      { headers: CSRF, data: { values_hash: part.values_hash } },
    );
    expect(again.status()).toBe(409);
    const second = page.locator(
      `[aria-labelledby="profile-section-${run.sections[1]!.section}"]`,
    );
    await second
      .getByRole("button", { name: "Skip section", exact: true })
      .click();
    await expect
      .poll(async () => (await read(page, id)).sections[1]!.state)
      .toBe("skipped");
    keep(
      "apply-a18",
      "saved-section.png",
      await page.screenshot({ fullPage: true }),
    );
    await shots(page, "saved-section");
    await page
      .getByRole("button", { name: "Cancel profile run", exact: true })
      .click();
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});

test("autosave asks first and preserves every existing site value", async ({
  browser,
}) => {
  const person = invented("a18-autosave");
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await stub.reset();
    await signUp(page, person);
    const run = await start(page, "portal.html?variant=autosave");
    await page.goto(`/site-profiles/${run.id}`);
    await expect(
      page.getByRole("button", { name: "I understand, fill empty fields" }),
    ).toBeVisible({ timeout: 180000 });
    expect(await stub.submissions()).toEqual([]);
    await page
      .getByRole("button", { name: "I understand, fill empty fields" })
      .click();
    await expect
      .poll(async () => (await read(page, run.id)).state, { timeout: 180000 })
      .toBe("done");
    const current = await read(page, run.id);
    expect(current.sections.map((part) => part.state)).toEqual([
      "saved",
      "saved",
      "saved",
    ]);
    const submissions = await stub.submissions();
    const fieldNames: Record<string, string> = {
      "Full name": "name",
      Employer: "employer",
      "Start date": "start",
      Degree: "degree",
      "End date": "graduation",
      School: "school",
    };
    for (const part of current.sections.slice(0, 2)) {
      const expected = Object.fromEntries(
        part.fields
          .filter((field) => fieldNames[field.label] && field.value)
          .map((field) => [fieldNames[field.label]!, String(field.value)]),
      );
      expect(Object.keys(expected).length).toBeGreaterThan(0);
      expect(
        submissions.some(
          (record) =>
            record.fields.section === part.label &&
            Object.entries(expected).every(
              ([name, value]) => record.fields[name] === value,
            ),
        ),
      ).toBe(true);
    }
    for (const record of submissions) {
      if (record.fields.section === "Experience 1")
        expect(record.fields.title).toBe("Site's old title");
      if (record.fields.section === "Contact") {
        expect(record.fields.phone).toBe("+33 1 00 00 00 00");
        expect(record.fields.email).toBe("e2e-portal@example.org");
      }
    }
    keep(
      "apply-a18",
      "autosave-report.png",
      await page.screenshot({ fullPage: true }),
    );
    await shots(page, "autosave-report");
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});

test("Workday-style pages reopen for Save readback and LinkedIn needs the person's opt in", async ({
  browser,
}) => {
  const person = invented("a18-known");
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page, [[409, /\/api\/site-profiles$/]]);
  try {
    await stub.reset();
    await signUp(page, person);
    const run = await start(page, "workday.html");
    await page.goto(`/site-profiles/${run.id}`);
    await expect
      .poll(async () => (await read(page, run.id)).state, { timeout: 180000 })
      .toBe("ready");
    const current = await read(page, run.id);
    expect(current.sections.map((part) => part.section)).toEqual([
      "experience",
      "education",
    ]);
    const card = page.locator('[aria-labelledby="profile-section-experience"]');
    await card
      .getByRole("button", { name: "Approve section", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Approve and save section", exact: true })
      .click();
    await expect
      .poll(async () => (await read(page, run.id)).sections[0]!.state, {
        timeout: 180000,
      })
      .toBe("saved");
    expect(await stub.submissions()).toHaveLength(1);
    await page.request.post(`/api/site-profiles/${run.id}/cancel`, {
      headers: CSRF,
    });
    await page.goto("/account");
    await page
      .getByLabel("Site address", { exact: true })
      .fill(SITE + "linkedin.html");
    await page
      .getByRole("button", {
        name: "Complete my profile on this site",
        exact: true,
      })
      .click();
    await expect(page.getByRole("alert")).toContainText("Turn on LinkedIn");
    keep(
      "apply-a18",
      "linkedin-refusal.png",
      await page.screenshot({ fullPage: true }),
    );
    await shots(page, "linkedin-refusal");
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});

test("a real Chromium value populated during pacing survives an autosave race", async ({
  browser,
}) => {
  const person = invented("a18-race");
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await stub.reset();
    await signUp(page, person);
    const run = await start(page, "portal.html?variant=autosave-race");
    await page.goto(`/site-profiles/${run.id}`);
    await page
      .getByRole("button", { name: "I understand, fill empty fields" })
      .click({ timeout: 180000 });
    await expect
      .poll(async () => (await read(page, run.id)).state, { timeout: 180000 })
      .toBe("ready");
    const current = await read(page, run.id);
    expect(current.sections[0]!.state).toBe("ready");
    expect(current.sections[0]!.error_code).toBe("value_differs");
    const saved = await stub.submissions();
    expect(
      saved
        .filter((record) => record.org === "site-profile-race")
        .map((record) => record.fields),
    ).toEqual([{ field: "employer", value: "Site added this value" }]);
    expect(
      saved.filter((record) => record.fields.section === "Experience 1"),
    ).toEqual([]);
    keep(
      "apply-a18",
      "autosave-race-kept.png",
      await page.screenshot({ fullPage: true }),
    );
    await shots(page, "autosave-race-kept");
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});
