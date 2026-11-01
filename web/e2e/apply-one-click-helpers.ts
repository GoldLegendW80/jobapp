import { expect, type APIRequestContext } from "@playwright/test";

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

/** Review first on the job page: the box ticked, so Apply stops at the filled form. */
export const reviewFirst = (request: APIRequestContext) =>
  setReviewBeforeSend(request, true);
