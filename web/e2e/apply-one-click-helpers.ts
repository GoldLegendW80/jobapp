import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { CSRF } from "./documents-helpers";

/**
 * Helpers of one click Apply (issue #738). The job page has one Apply button: with the person's
 * "Let me review each filled form before it is sent" box unticked (the default) the assistant
 * fills the form and the app sends it; ticked, the fill stops for Approve and submit. A journey
 * that walks the Review first path ticks the box through the api first, then clicks Apply.
 */

/** The person's review box, saved through PUT /api/apply/settings. */
export async function setReviewBeforeSend(
  request: APIRequestContext,
  review: boolean,
) {
  const saved = await request.put("/api/apply/settings", {
    headers: CSRF,
    data: { review_before_send: review },
  });
  expect(saved.status()).toBe(200);
  const body = (await saved.json()) as { review_before_send: boolean };
  expect(body.review_before_send).toBe(review);
}

/**
 * The 2026-11-01 Review first fill (adapters, answers, the screenshot, Approve and submit) on the
 * job page open in `page`. Since A20 the Apply button with the box ticked runs the apply agent
 * and its one Confirm instead (apply-one-click.spec.ts), so the journeys of that older build,
 * which Likes still use, start it through the api with the explicit mode `review`, then reload.
 */
export async function startReviewFirst(page: Page) {
  const job = /^\/jobs\/([^/?#]+)/.exec(new URL(page.url()).pathname)?.[1];
  expect(job, "the job page is open").toBeTruthy();
  const started = await page.request.post(
    `/api/postings/${job ?? ""}/applications`,
    { headers: CSRF, data: { mode: "review", dry_run: false } },
  );
  expect(started.status()).toBe(201);
  await page.reload();
}
