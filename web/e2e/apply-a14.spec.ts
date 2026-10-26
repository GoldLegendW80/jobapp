import { expect, test } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import {
  CSRF,
  addPosting,
  invented,
  signUp,
  waitForRender,
} from "./documents-helpers";
import { allowOnConsentPage, stub, stubUrl } from "./mail-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

test.setTimeout(300_000);

test("review confirms a posting recipient and sends exactly the fixed PDFs through MCP", async ({
  browser,
}) => {
  if (!stubUrl)
    throw new Error(
      "set E2E_GMAIL_STUB_URL and start the fake Gmail A14 stack",
    );
  const person = invented("a14");
  const mailbox = `e2e-a14-${Date.now().toString(36)}@example.org`;
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await stub.seed({ email: mailbox, messages: [] });
    await signUp(page, person);
    await page.request.put("/api/me/model-credential", {
      headers: CSRF,
      data: {
        kind: "anthropic_api_key",
        secret: `sk-ant-api03-e2egood${"Fa14".repeat(20)}Rn42`,
      },
    });
    const consent = await page.request.get("/api/documents/consent");
    const { version } = (await consent.json()) as { version: number };
    expect(
      (
        await page.request.post("/api/documents/consent", {
          headers: CSRF,
          data: { version },
        })
      ).status(),
    ).toBe(200);
    const built = await page.request.post("/api/documents/base-cvs", {
      headers: CSRF,
      data: { from: "profile", script: "documents_base_journey" },
    });
    const { base_cv: base } = (await built.json()) as {
      base_cv: { id: string };
    };
    await waitForRender(page.request, base.id, 1);
    await page.goto("/account");
    await page.getByRole("button", { name: "Connect Gmail" }).click();
    const connect = page.getByRole("dialog", { name: "Connect Gmail" });
    await connect
      .getByRole("checkbox", { name: "Let the assistant read my job mails" })
      .check();
    await connect.getByRole("button", { name: "Continue to Google" }).click();
    await allowOnConsentPage(page, mailbox);
    await expect(page.getByText(mailbox, { exact: true })).toBeVisible();
    const job = await addPosting(page.request, {
      ...person,
      posting: {
        ...person.posting,
        posting_text:
          person.posting.posting_text +
          "\nSend your CV to e2e-jobs@example.org.",
      },
    });
    const written = await page.request.post(
      `/api/jobs/${job}/documents/write`,
      {
        headers: CSRF,
        data: { kinds: ["cv", "letter"], script: "journey" },
      },
    );
    expect(written.status()).toBe(202);
    await waitForPostingDocuments(page.request, job);
    await page.goto(`/jobs/${job}`);
    const section = page.getByRole("region", { name: "Apply", exact: true });
    await section.getByRole("button", { name: "Apply (Review first)" }).click();
    await expect(
      section.getByRole("button", { name: "Approve and submit" }),
    ).toBeVisible({ timeout: 90_000 });
    // The person's exact text is checked by the same fact-check gate before sending.
    const applicationResponse = await page.request.get(
      `/api/applications?posting_id=${job}`,
    );
    const applications = (await applicationResponse.json()) as {
      items: Array<{ id: string }>;
    };
    const application = applications.items[0];
    if (!application) throw new Error("email application was not created");
    const applicationId = application.id;
    expect(
      (
        await page.request.put(
          `/api/applications/${applicationId}/answers/email_body`,
          {
            headers: CSRF,
            data: { value: "Please find my application attached." },
          },
        )
      ).status(),
    ).toBe(200);
    await page.reload();
    await section.getByRole("button", { name: "Approve and submit" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("e2e-jobs@example.org");
    await expect(dialog).toContainText("Please find my application attached.");
    await expect(dialog).toContainText("CV version");
    const submit = dialog.getByRole("button", {
      name: "Approve and submit",
      exact: true,
    });
    await expect(submit).toBeDisabled();
    await dialog
      .getByRole("checkbox", { name: /I confirm this new email recipient/ })
      .check();
    await submit.click();
    await expect.poll(async () => (await stub.sent(mailbox)).length).toBe(1);
    const [sent] = await stub.sent(mailbox);
    if (!sent)
      throw new Error("fake Gmail did not receive the approved application");
    const raw = Buffer.from(sent.raw, "base64url").toString("utf8");
    expect(raw).toContain("e2e-jobs@example.org");
    expect(raw).toContain('filename="cv.pdf"');
    expect(raw).toContain('filename="letter.pdf"');
    expect(raw).toContain("X-Jobapp-Send:");
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});
