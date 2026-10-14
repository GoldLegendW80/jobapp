"""What every tool of B5 does to a page before and around its action, in one place:

- `snapshot_for_ai`, the ONE call of Playwright's private AI snapshot (refs `[ref=eN]`);
- `front`, the tab the tool acts on brought to the front (B1: Chromium throttles background
  tabs, a 0.9 s fill took 5.9 s in a background tab);
- `navigate`, a navigation with its own timeout that answers a failure result instead of hanging
  (B1: once in five runs a slow page's second tab never reached `load`).
"""

from typing import Any

from browserd.config import NAVIGATION_S

# browser/Dockerfile pins the same; bump both on purpose only.
PLAYWRIGHT_VERSION = "1.55.0"
SNAPSHOT_METHOD = "snapshotForAI"
REF_MARKER = "[ref=e"


class SnapshotUnavailable(RuntimeError):
    """The pinned Playwright no longer gives the AI snapshot with refs."""


async def snapshot_for_ai(page: Any) -> str:
    """Playwright MCP's snapshot format, with element refs (`[ref=e8]`), from Playwright's own
    `snapshotForAI` protocol method. The Python client of 1.55 has no public wrapper (the Node
    client has `page._snapshotForAI()`), so the method is sent on the page's channel: a private
    object and an undocumented method. That is why it lives here and nowhere else, why
    Playwright is pinned, and why api/tests/browser/test_browser_container.py fails loudly when
    the method is missing or its answer has no ref.

    Fallback, if a Playwright bump removes it: run the tool layer on the Node client, where
    `_snapshotForAI` is a named method, or take the documented `locator.aria_snapshot()` and
    compute the refs from locators (B1's evidence, snapshot-locator.txt)."""
    channel = page._impl_obj._channel
    try:
        answer = await channel.send(SNAPSHOT_METHOD, None, {"timeout": 10000})
    except Exception as exc:
        raise SnapshotUnavailable(
            f"Playwright {PLAYWRIGHT_VERSION}: {SNAPSHOT_METHOD} failed ({exc}); see the fallback"
            " in browserd/pages.py"
        ) from exc
    text = answer if isinstance(answer, str) else (answer or {}).get("snapshot", "")
    if REF_MARKER not in text:
        raise SnapshotUnavailable(
            f"{SNAPSHOT_METHOD} answered without any {REF_MARKER}N] ref"
        )
    return text


async def front(page: Any) -> Any:
    await page.bring_to_front()
    return page


async def navigate(
    page: Any, url: str, timeout_s: float = NAVIGATION_S
) -> dict[str, Any]:
    """Navigates the tab (brought to the front first) and waits for `load`, at most timeout_s.
    Past it, or on a network error, the page is stopped and a failure result is returned."""
    from playwright.async_api import Error
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    await front(page)
    try:
        response = await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
    except PlaywrightTimeout:
        try:
            await page.evaluate("window.stop()")
        except Error:
            pass
        return {
            "ok": False,
            "error": "navigation_timeout",
            "timeout_s": timeout_s,
            "url": page.url,
        }
    except Error as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "error"
        return {
            "ok": False,
            "error": "navigation_failed",
            "detail": detail[:200],
            "url": page.url,
        }
    return {
        "ok": True,
        "status": response.status if response else None,
        "url": page.url,
        "title": await page.title(),
    }
