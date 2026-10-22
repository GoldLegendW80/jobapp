import { randomUUID } from "node:crypto";

import { expect, test, type APIRequestContext } from "@playwright/test";

import { atsFormsUrl, stub } from "./apply-helpers";
import {
  CSRF,
  addPosting,
  invented,
  signUp,
  waitForRender,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

// Registered by apply-all.config.ts's apply- prefix. Two isolated invented people, the
// local fake Greenhouse form, and one explicit UI approval. No real account or site.
test.describe.configure({ timeout: 600_000 });
const shots = makeShots("apply-a17", "two-users");

type Approval = {
  answers_hash: string;
  answers: Array<{ field_key: string; value: unknown }>;
  screenshots: Array<{ id: string }>;
};

async function denied(
  request: APIRequestContext,
  paths: string[],
  status: number,
  marker: string,
) {
  for (const path of paths) {
    const response = await request.get(path);
    expect(response.status()).toBe(status);
    expect(response.headers()["cache-control"]).toBe("no-store");
    expect((await response.body()).includes(Buffer.from(marker))).toBe(false);
  }
}

test("A approves one fake submission; B and a signed-out person cannot read its private views", async ({
  browser,
}) => {
  const owner = invented("a17-owner");
  const other = invented("a17-other");
  const ownerContext = await newContext(browser);
  const otherContext = await newContext(browser);
  const signedOut = await newContext(browser);
  const page = await ownerContext.newPage();
  const otherPage = await otherContext.newPage();
  const errors = watchErrors(page);
  const marker = `a17-${randomUUID()}`;
  try {
    await stub.reset();
    await signUp(page, owner);
    await signUp(otherPage, other);
    const credential = await page.request.put("/api/me/model-credential", {
      headers: CSRF,
      data: {
        kind: "anthropic_api_key",
        secret: `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`,
      },
    });
    expect(credential.status()).toBe(200);
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
    const base = await page.request.post("/api/documents/base-cvs", {
      headers: CSRF,
      data: { from: "profile", script: "documents_base_journey" },
    });
    expect(base.status()).toBe(202);
    const { base_cv } = (await base.json()) as { base_cv: { id: string } };
    await waitForRender(page.request, base_cv.id, 1);

    const posting = await addPosting(page.request, owner);
    const current = await page.request.get(`/api/jobs/${posting}`);
    const { updated_at } = (await current.json()) as { updated_at: string };
    const url = (await stub.posting("greenhouse")).replace(
      atsFormsUrl ?? "",
      "http://atsforms:8080",
    );
    expect(
      (
        await page.request.patch(`/api/jobs/${posting}`, {
          headers: CSRF,
          data: { url, fit: 90, updated_at },
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await page.request.post(`/api/jobs/${posting}/documents/write`, {
          headers: CSRF,
          data: { kinds: ["cv", "letter"], script: "journey" },
        })
      ).status(),
    ).toBe(202);
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/api/jobs/${posting}/documents`,
          );
          const { documents } = (await response.json()) as {
            documents: Array<{ latest: unknown }>;
          };
          return (
            documents.length === 2 && documents.every((d) => d.latest !== null)
          );
        },
        { timeout: 150_000 },
      )
      .toBe(true);

    await page.goto(`/jobs/${posting}`);
    const section = page.getByRole("region", { name: "Apply", exact: true });
    await section.getByRole("button", { name: "Apply (Review first)" }).click();
    await expect(
      section.getByRole("img", { name: /before the click/ }),
    ).toBeVisible({
      timeout: 180_000,
    });
    const open = section.locator("li[data-left=true]").filter({
      has: page.locator("input, textarea, select, [role=group] button"),
    });
    while ((await open.count()) > 0) {
      const count = await open.count();
      const row = open.first();
      if (await row.getByRole("textbox").count()) {
        await row.getByRole("textbox").fill("Not applicable");
        await row.getByRole("button", { name: "Save answer" }).click();
      } else if (await row.getByRole("combobox").count()) {
        await row.getByRole("combobox").selectOption({ index: 1 });
      } else {
        await row.getByRole("button", { name: "No", exact: true }).click();
      }
      await expect(open).toHaveCount(count - 1);
    }
    await section
      .getByRole("button", { name: "Edit: First Name", exact: true })
      .click();
    const edited = section.locator("li").filter({
      has: page.getByRole("textbox", {
        name: "Answer for First Name",
        exact: true,
      }),
    });
    await edited.getByRole("textbox").fill(marker);
    await edited.getByRole("button", { name: "Save answer" }).click();

    const listed = await page.request.get(
      `/api/applications?posting_id=${posting}`,
    );
    const { items } = (await listed.json()) as { items: Array<{ id: string }> };
    expect(items).toHaveLength(1);
    const application = items[0]!.id;
    const approvalPath = `/api/applications/${application}/approval`;
    const read = await page.request.get(approvalPath);
    expect(read.status()).toBe(200);
    expect(read.headers()["cache-control"]).toBe("no-store");
    const approval = (await read.json()) as Approval;
    expect(approval.answers.some((answer) => answer.value === marker)).toBe(
      true,
    );
    const paths = [
      `/api/applications/${application}`,
      approvalPath,
      ...approval.screenshots.map(
        (shot) => `/api/applications/${application}/screenshots/${shot.id}`,
      ),
    ];
    expect(approval.screenshots.length).toBeGreaterThan(0);
    await denied(otherPage.request, paths, 404, marker);
    await denied(signedOut.request, paths, 401, marker);
    expect(
      (
        await otherPage.request.post(
          `/api/applications/${application}/approve`,
          {
            headers: CSRF,
            data: { answers_hash: approval.answers_hash },
          },
        )
      ).status(),
    ).toBe(404);
    expect(
      (await stub.submissions()).filter((one) => one.accepted),
    ).toHaveLength(0);

    await expect(section).toContainText(marker);
    await expect(section).toContainText(
      "Filled and waiting for your approval.",
    );
    await expect(
      section.getByRole("button", { name: "Approve and submit", exact: true }),
    ).toBeEnabled();
    await shots(page, "owner-review");
    await otherPage.goto(`/jobs/${posting}`);
    await expect(
      otherPage.getByRole("heading", { name: "Page not found", exact: true }),
    ).toBeVisible();
    await expect(otherPage.locator("body")).not.toContainText(marker);
    await shots(otherPage, "other-denied");
    await expect(otherPage.locator("body")).not.toContainText(marker);

    await section.getByRole("button", { name: "Approve and submit" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Submit this application to");
    await dialog.getByRole("button", { name: "Approve and submit" }).click();
    await expect(section).toContainText("Applied.", { timeout: 180_000 });
    const accepted = (await stub.submissions()).filter((one) => one.accepted);
    expect(accepted).toHaveLength(1);
    expect(Object.values(accepted[0]!.fields).flat().includes(marker)).toBe(
      true,
    );
    await denied(otherPage.request, paths, 404, marker);
    await denied(signedOut.request, paths, 401, marker);
    expect(errors).toEqual([]);
  } finally {
    await ownerContext.close();
    await otherContext.close();
    await signedOut.close();
    clearAttempts([owner.email, other.email]);
  }
});
