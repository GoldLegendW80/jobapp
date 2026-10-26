import { expect, test } from "@playwright/test";

import { makeShots } from "./stack";

const shots = makeShots("apply-shot-readiness", "shot-readiness");

test("screenshots wait for a delayed lazy image below the viewport", async ({
  page,
}) => {
  let releaseImage = () => {};
  const released = new Promise<void>((resolve) => {
    releaseImage = resolve;
  });
  let imageRequested = () => {};
  const requested = new Promise<void>((resolve) => {
    imageRequested = resolve;
  });
  await page.route("http://127.0.0.1/shot-readiness.svg", async (route) => {
    imageRequested();
    await released;
    await route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="green"/></svg>',
    });
  });
  await page.setContent(
    `<style>body { margin: 0 } img { display: block; max-width: 100% }</style>
     <div style="height: 12000px">Application evidence</div>
     <img loading="lazy" src="http://127.0.0.1/shot-readiness.svg" alt="Application form">
     <section>Held for review</section>`,
    { waitUntil: "domcontentloaded" },
  );
  expect(
    await page.locator("img").evaluate((img: HTMLImageElement) => img.complete),
  ).toBe(false);

  const captures: number[] = [];
  const capture = page.screenshot.bind(page);
  page.screenshot = async (options) => {
    const image = await page
      .locator("img")
      .evaluate((img: HTMLImageElement) => ({
        complete: img.complete,
        width: img.naturalWidth,
        height: img.getBoundingClientRect().height,
        bottom: img.getBoundingClientRect().bottom,
        heldTop: document.querySelector("section")!.getBoundingClientRect().top,
      }));
    captures.push(image.height);
    expect(image.complete).toBe(true);
    expect(image.width).toBe(400);
    expect(image.height).toBeGreaterThan(0);
    expect(image.heldTop).toBe(image.bottom);
    return capture(options);
  };

  // Keep the response pending until readiness has explicitly requested the offscreen image.
  const pending = shots(page, "delayed").then(
    () => null,
    (error: unknown) => error,
  );
  try {
    expect(
      await Promise.race([
        requested.then(() => "requested"),
        pending.then(() => "finished"),
      ]),
    ).toBe("requested");
    expect(captures).toEqual([]);
  } finally {
    releaseImage();
  }
  expect(await pending).toBeNull();
  expect(captures).toHaveLength(4);
});

test("screenshots reject a broken rendered image", async ({ page }) => {
  await page.route("http://127.0.0.1/shot-broken.svg", (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: "broken image" }),
  );
  await page.setContent(
    '<img src="http://127.0.0.1/shot-broken.svg" alt="Application form">',
  );
  await expect(shots(page, "broken")).rejects.toThrow(/decod/i);
});
