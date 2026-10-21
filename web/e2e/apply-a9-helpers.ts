// Helpers of the Apply myself journey (apply A9): the person's mouse and keyboard reach the form
// only through the live view's canvas, as in a real session. Where an element of the form stands
// is read inside the browser container over the lease's local CDP (an `apply_manual` lease keeps
// its debugging port; nothing leaves the container), as browser-b4-helpers.ts reads a value.
// Every call names the journey's own compose project (E2E_COMPOSE_PROJECT), never the app's.
import { execFileSync } from "node:child_process";

import { expect, type Page } from "@playwright/test";

import { root } from "./stack";

function compose(...args: string[]): string {
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!project)
    throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project,
      "-f",
      "compose.yml",
      "--profile",
      "browser",
      ...args,
    ],
    { cwd: root, encoding: "utf8", timeout: 120_000 },
  );
}

// Scrolls the element to the middle of the window (the person's scroll wheel) and answers its
// centre on the X screen: the window's place, its toolbars, then the element in the page.
const WHERE = `
import asyncio, json, sys
from browserd import probe
from playwright.async_api import async_playwright

async def main(selector):
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{probe.cdp_port()}")
        page = b.contexts[0].pages[0]
        found = await page.locator(selector).first.evaluate(
            """(el) => {
                el.scrollIntoView({ block: "center" });
                const r = el.getBoundingClientRect();
                return {
                    x: window.screenX + r.left + r.width / 2,
                    y: window.screenY + (window.outerHeight - window.innerHeight) + r.top + r.height / 2,
                };
            }""",
        )
        print(json.dumps(found))

asyncio.run(main(sys.argv[1]))
`;

/** Where the centre of the first element `selector` of the form stands on the remote screen. */
export function remoteSpot(selector: string): { x: number; y: number } {
  const out = compose("exec", "-T", "browser", "python", "-c", WHERE, selector);
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as {
    x: number;
    y: number;
  };
}

/**
 * The person clicks the element `selector` of the form through the live view: the remote screen's
 * point, scaled to the canvas noVNC draws it on.
 */
export async function clickInView(page: Page, selector: string): Promise<void> {
  const spot = remoteSpot(selector);
  const canvas = page.getByTestId("live-screen").locator("canvas").first();
  const box = await canvas.boundingBox();
  const size = await canvas.evaluate((c: HTMLCanvasElement) => ({
    width: c.width,
    height: c.height,
  }));
  expect(box).not.toBeNull();
  if (!box) return;
  await page.mouse.click(
    box.x + (spot.x * box.width) / size.width,
    box.y + (spot.y * box.height) / size.height,
  );
}
