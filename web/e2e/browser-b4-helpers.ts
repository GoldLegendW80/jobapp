// Helpers of the live view journey (browser B4): what the lease's Chromium holds, read inside the
// browser container over its local CDP (which never leaves it), and Caddy's access log of the
// journey's own compose project (E2E_COMPOSE_PROJECT).
import { execFileSync } from "node:child_process";

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

/** The value of the field `selector` in the front tab of the lease's Chromium. */
export function fieldValue(selector: string): string {
  const code = [
    "import asyncio, sys",
    "from browserd import probe",
    "from playwright.async_api import async_playwright",
    "async def main():",
    "    async with async_playwright() as pw:",
    "        b = await pw.chromium.connect_over_cdp(f'http://127.0.0.1:{probe.cdp_port()}')",
    "        page = b.contexts[0].pages[0]",
    "        print(await page.input_value(sys.argv[1], timeout=5000))",
    "asyncio.run(main())",
  ].join("\n");
  return compose(
    "exec",
    "-T",
    "browser",
    "python",
    "-c",
    code,
    selector,
  ).trim();
}

/** Caddy's whole log of the journey's stack. */
export function caddyLog(): string {
  return compose("logs", "--no-color", "caddy");
}
