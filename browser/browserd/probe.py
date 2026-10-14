"""The development and test path into the current lease's Chromium, run inside the container
only: `docker compose exec -T browser python -m browserd.probe <action> [args]`. It reads the
CDP port from browserd's root only state file; nothing of it is reachable from the network and
no agent ever runs it (SPEC-browser: CDP never leaves the container). It prints one JSON line.

  snapshot <url>           the AI snapshot through browserd.pages.snapshot_for_ai
  navigate <url> <secs>    browserd.pages.navigate with that timeout
  sandbox                  the text of chrome://sandbox
  env <url>                navigator.languages, the user agent, the client hints, WebGL, zone
  front <url>              a second tab opened by the page, then the first acted on: its
                           visibility and focus after pages.front
"""

import asyncio
import json
import sys

from browserd import config as c
from browserd import pages


def cdp_port() -> int:
    with open(f"{c.STATE_DIR}/current.json") as handle:
        return json.load(handle)["port"]


async def body_text(page) -> str:
    try:
        return (await page.inner_text("body", timeout=5000))[:2000]
    except Exception:  # noqa: BLE001 - an error page or none: no text
        return ""


async def run(action: str, args: list[str]) -> dict:
    from playwright.async_api import async_playwright

    port = cdp_port()
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        if action == "snapshot":
            await pages.navigate(page, args[0])
            return {"snapshot": await pages.snapshot_for_ai(page)}
        if action == "navigate":
            result = await pages.navigate(page, args[0], float(args[1]))
            return {**result, "text": await body_text(page)}
        if action == "sandbox":
            await page.goto("chrome://sandbox")
            return {"text": await page.inner_text("body")}
        if action == "env":
            result = await pages.navigate(page, args[0])
            echoed = json.loads(await body_text(page) or "{}")
            return {
                "navigate": result,
                "accept_language": echoed.get("accept-language", ""),
                **await page.evaluate(
                    """async () => {
                      const hints = navigator.userAgentData
                        ? await navigator.userAgentData.getHighEntropyValues(["architecture", "platform"])
                        : {};
                      const canvas = document.createElement("canvas");
                      return {
                        languages: navigator.languages, userAgent: navigator.userAgent,
                        architecture: hints.architecture, platform: hints.platform,
                        webgl: !!canvas.getContext("webgl"),
                        zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        webdriver: navigator.webdriver,
                      };
                    }"""
                ),
            }
        if action == "front":
            first = page
            await pages.navigate(first, args[0])
            # A tab the page opens itself (window.open): the first one goes to the background.
            async with context.expect_page() as opened:
                await first.evaluate("url => window.open(url)", args[0])
            second = await opened.value
            await second.wait_for_load_state()
            await pages.front(first)
            after = await first.evaluate(
                "[document.visibilityState, document.hasFocus()]"
            )
            await second.close()
            return {"visibility": after[0], "focus": after[1]}
        raise SystemExit(f"unknown action {action}")


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(sys.argv[1], sys.argv[2:]))))
