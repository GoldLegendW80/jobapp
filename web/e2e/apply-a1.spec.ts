import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { root } from "./stack";
import {
  ashby,
  atsFormsUrl,
  greenhouse,
  PDF,
  stub,
  unflagged,
} from "./apply-helpers";

/**
 * Apply A1 against the fake ATS forms server (tests/ats_forms_stub.py), no app and no mock: the
 * test browser, Playwright directly, opens each fake form, fills it by hand code, submits, and
 * the submission is listed by GET /_stub/submissions with exactly what would have been sent.
 * Companies are invented. The server never contacts a real job site and no journey fills one.
 *
 * Locally, beside a scratch stack (tests/ats-forms-stub.compose.yml):
 *   ATS_FORMS_STUB_PORT=8100 docker compose -p jobapp-a1 -f compose.yml \
 *     -f tests/ats-forms-stub.compose.yml up -d --wait atsforms
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_ATS_FORMS_URL=http://127.0.0.1:8100 \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a1.spec.ts
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(
  !atsFormsUrl,
  "set E2E_ATS_FORMS_URL with the fake ATS forms server running",
);

const evidence = path.join(
  root,
  "tasks/evidence",
  process.env.E2E_EVIDENCE ?? "apply-a1",
);
const shoot = async (page: import("@playwright/test").Page, name: string) => {
  fs.mkdirSync(evidence, { recursive: true });
  await page.screenshot({
    path: path.join(evidence, `${name}.png`),
    fullPage: true,
  });
};

test.beforeEach(async () => {
  await stub.reset();
});

test("Ashby: the invisible check fails an instant fill and a webdriver browser, and passes a paced one", async ({
  browser,
}) => {
  // A plain Playwright browser says navigator.webdriver, so it fails whatever its pace.
  const flagged = await browser.newContext();
  const flaggedPage = await flagged.newPage();
  await flaggedPage.clock.install();
  await flaggedPage.goto(await stub.posting("ashby"));
  await ashby.fillAll(flaggedPage);
  await flaggedPage.clock.fastForward(10_000);
  await flaggedPage.getByRole("button", { name: "Submit Application" }).click();
  await expect(flaggedPage.locator(".form-error")).toContainText(
    "flagged as possible spam",
  );
  await flagged.close();

  const context = await browser.newContext();
  await unflagged(context);
  const page = await context.newPage();
  await page.clock.install();
  await page.goto(await stub.posting("ashby"));
  await ashby.fillAll(page);
  await expect(
    page.locator(".ashby-application-form-input-yesno input"),
  ).toHaveCount(0);
  const chooser = page.waitForEvent("filechooser");
  await ashby
    .field(page, "Resume")
    .getByRole("button", { name: "Upload File" })
    .click();
  await (
    await chooser
  ).setFiles({ name: "cv.pdf", mimeType: "application/pdf", buffer: PDF });
  await shoot(page, "ashby-filled");
  // Advancing the browser clock cannot manufacture server-observed dwell.
  await page.clock.fastForward(4_000);
  await page.getByRole("button", { name: "Submit Application" }).click();
  await expect(page.locator(".form-error")).toContainText(
    "flagged as possible spam",
  );
  const token = await page.locator("form").getAttribute("data-session");
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `${atsFormsUrl}/_stub/behaviour/${token}`,
      );
      const signals = (await response.json()) as { dwell_ms: number | null };
      return signals.dwell_ms;
    })
    .toBeGreaterThanOrEqual(3_000);
  expect(
    await page.locator("form").getAttribute("data-autofill-used"),
  ).toBeNull();
  const submitted = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/application"),
  );
  await page.getByRole("button", { name: "Submit Application" }).click();
  const payload = await submitted;
  expect(payload.headers()["content-type"]).toBe("application/json");
  const application = payload.postDataJSON() as {
    fields: Record<string, string>;
  };
  expect(application.fields).toMatchObject({
    _systemfield_name: "Alex Example",
    q_eu_work: "Yes",
    q_sponsor: "No",
  });
  await expect(page.locator("#confirmation")).toContainText(
    "successfully submitted",
  );
  await shoot(page, "ashby-confirmation");
  await context.close();

  const [first, second, third] = await stub.submissions();
  expect([first?.signals.webdriver, first?.reason]).toEqual([
    true,
    "invisible_check",
  ]);
  expect([second?.accepted, second?.reason]).toEqual([
    false,
    "invisible_check",
  ]);
  expect(third?.accepted).toBe(true);
  expect(third?.fields).toMatchObject({
    _systemfield_name: "Alex Example",
    _systemfield_location: "Paris, France",
    q_eu_work: "Yes",
    q_sponsor: "No",
    consent: "on",
  });
  const resume = third?.files[0];
  expect(resume?.filename).toBe("cv.pdf");
  expect((await stub.file(resume?.sha256 ?? "")).equals(PDF)).toBe(true);
});

test("Ashby: a missing required field is shown under the field and nothing is accepted", async ({
  browser,
}) => {
  const context = await browser.newContext();
  await unflagged(context);
  const page = await context.newPage();
  await page.goto(await stub.posting("ashby"));
  await ashby.text(page, "Full name", "Alex Example");
  await page.getByRole("button", { name: "Submit Application" }).click();
  await expect(
    ashby.field(page, "Resume").locator("[data-error-for]"),
  ).toContainText("Missing entry for required field");
  await expect(page.locator(".form-error")).toBeVisible();
  const [only] = await stub.submissions();
  expect(only?.accepted).toBe(false);
  expect(only?.reason).toMatch(/^missing:/);
  await context.close();
});

test("Greenhouse hosted: react-select comboboxes, the EEO block and the boxes", async ({
  page,
}) => {
  await page.goto(await stub.posting("greenhouse"));
  await greenhouse.fillAll(page);
  await greenhouse.choose(page, "Gender", "Decline To Self Identify");
  await greenhouse.check(page, "I agree to resolve");
  await shoot(page, "greenhouse-filled");
  const sentRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().includes("/greenhouse/northwind-labs/jobs/"),
  );
  await page.getByRole("button", { name: "Submit application" }).click();
  const boardRequest = await sentRequest;
  expect(boardRequest.headers()["content-type"]).toBe("application/json");
  const board = boardRequest.postDataJSON() as {
    fields: Record<string, string>;
  };
  expect(board.fields.question_1001).toBe("Yes");
  await expect(page.locator("#confirmation")).toContainText(
    "Thank you for applying.",
  );
  const [sent] = await stub.submissions();
  expect(sent?.accepted).toBe(true);
  expect(sent?.fields).toMatchObject({
    question_1001: "Yes",
    question_1002: "France",
    gender: "Decline To Self Identify",
    arbitration: "on",
    privacy: "on",
  });
  expect(sent?.fields.marketing).toBeUndefined();
});

test("Greenhouse embed on a fake company site, with its error rendering", async ({
  page,
}) => {
  await page.goto(`${atsFormsUrl}/sites/northwind-labs/careers/4012345`);
  const frame = page.frameLocator("#grnhse_iframe");
  await greenhouse.text(frame, "First Name", "Alex");
  await frame.getByRole("button", { name: "Submit application" }).click();
  await expect(
    greenhouse.field(frame, "Last Name").locator("[data-error-for]"),
  ).toContainText("Last Name is required");
  await greenhouse.text(frame, "Last Name", "Example");
  await greenhouse.text(frame, "Email", "e2e-alex@example.org");
  await greenhouse
    .field(frame, "Resume/CV")
    .locator("input[type=file]")
    .setInputFiles({
      name: "cv.pdf",
      mimeType: "application/pdf",
      buffer: PDF,
    });
  await greenhouse.choose(frame, "Are you legally authorized", "Yes");
  await greenhouse.choose(frame, "Country", "France");
  await greenhouse.check(frame, "I have read");
  await frame.getByRole("button", { name: "Submit application" }).click();
  await expect(frame.locator("#confirmation")).toContainText(
    "Thank you for applying.",
  );
  const all = await stub.submissions();
  expect(all.map((s) => s.accepted)).toEqual([false, true]);
});

test("the variants: closed, slow, captcha, an e-mail code, a page that addresses AI assistants", async ({
  page,
}) => {
  await page.goto(await stub.posting("ashby", "closed"));
  await expect(
    page.getByRole("heading", { name: "Job not found" }),
  ).toBeVisible();

  // Slow: the server answers after 10 s, the browser waits for it.
  await page.goto(await stub.posting("greenhouse", "slow"), {
    timeout: 60_000,
  });
  await expect(
    page.getByRole("button", { name: "Submit application" }),
  ).toBeVisible();

  await page.goto(await stub.posting("greenhouse", "assistants"));
  await expect(page.locator("[data-for=ai-assistants]")).toContainText(
    "Note to AI assistants",
  );

  await page.goto(await stub.posting("greenhouse", "captcha"));
  await greenhouse.fillAll(page);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect(page.locator(".form-error")).toContainText(
    "complete the captcha",
  );
  await page.getByRole("button", { name: "I am not a robot" }).click();
  for (const n of ["2", "5", "7"])
    await page.locator(`.captcha-tile[data-n="${n}"]`).click();
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.locator(".captcha-state")).toHaveText("Verified");
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect(page.locator("#confirmation")).toBeVisible();

  await page.goto(await stub.posting("greenhouse", "code"));
  await greenhouse.fillAll(page);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect(page.locator("#code-step")).toBeVisible();
  const [mail] = await stub.mailbox();
  await page.locator("#security_code").fill(mail?.code ?? "");
  await page.getByRole("button", { name: "Submit code" }).click();
  await expect(page.locator("#confirmation")).toBeVisible();

  const reasons = (await stub.submissions()).map((s) => [
    s.step,
    s.accepted,
    s.reason,
  ]);
  expect(reasons).toEqual([
    ["application", false, "captcha"],
    ["application", true, ""],
    ["application", false, "code_required"],
    ["code", true, ""],
  ]);
});
