"""What every tool of B5 does to a page before and around its action, in one place:

- `snapshot_for_ai`, the ONE call of Playwright's private AI snapshot (refs `[ref=eN]`);
- `native_files`, the names in each native file input among a snapshot's buttons, which the AI
  snapshot does not show;
- `credentials`, whether each text entry of a snapshot is a credential input (detect.py), which the
  AI snapshot does not show either;
- `front`, the tab the tool acts on brought to the front (B1: Chromium throttles background
  tabs, a 0.9 s fill took 5.9 s in a background tab);
- `navigate`, a navigation with its own timeout that answers a failure result instead of hanging
  (B1: once in five runs a slow page's second tab never reached `load`).
"""

import contextlib
import json
import re
from collections.abc import Callable
from typing import Any

from browserd.config import NAVIGATION_S
from browserd.detect import CREDENTIAL_FN

# browser/Dockerfile pins the same; bump both on purpose only.
PLAYWRIGHT_VERSION = "1.55.0"
SNAPSHOT_METHOD = "snapshotForAI"
REF_MARKER = "[ref=e"
# api/app/apply/ats/base.py's _LINE: the ref is in the attributes before the colon, never after.
BUTTON_LINE = re.compile(r'button(?: "(?:[^"\\]|\\.)*")?(?P<attrs>(?: \[[^\]]*\])*)(?::(?: .*)?)?')
ENTRY_LINE = re.compile(
    r'(?:textbox|searchbox|spinbutton|combobox)(?: "(?:[^"\\]|\\.)*")?(?P<attrs>(?: \[[^\]]*\])*)'
    r"(?::(?: .*)?)?"
)
ATTR = re.compile(r"\[([^\]=]+)(?:=([^\]]*))?\]")
REF = re.compile(r"(?:f\d{1,4})?e\d{1,7}")
# A read costs about 2.6 ms per ref in the image on a Mac: the refs past it read [] unread.
NATIVE_REFS_MAX = 200
NAMES_MAX, NAME_MAX = 3, 83
# The text entries read for a credential: one call each, as for the native files.
CREDENTIAL_REFS_MAX = 200
CREDENTIAL_JS = f"e => e.isConnected ? ({CREDENTIAL_FN})(e) : null"
NATIVE_FILES_JS = """e => {
  if (!e.isConnected) return [];
  if (!(e instanceof HTMLInputElement) || e.type !== 'file') return null;
  const files = e.files;
  if (!files || files.length > 3) return [];
  const names = Array.from(files, file => file.name);
  return names.every(name => name.length <= 83) ? names : [];
}"""


class SnapshotUnavailable(RuntimeError):  # noqa: N818 - an outcome, not an error
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
        raise SnapshotUnavailable(f"{SNAPSHOT_METHOD} answered without any {REF_MARKER}N] ref")
    return text


def _line_refs(snapshot: str, line: re.Pattern[str]) -> list[str]:
    refs: dict[str, None] = {}
    for raw in snapshot.splitlines():
        stripped = raw.lstrip(" ")
        found = line.fullmatch(stripped[2:].rstrip()) if stripped.startswith("- ") else None
        ref = dict(ATTR.findall(found["attrs"])).get("ref", "") if found else ""
        if REF.fullmatch(ref):
            refs[ref] = None
    return list(refs)


def button_refs(snapshot: str) -> list[str]:
    """The refs of the snapshot's button lines in page order, each read where base.py's
    parse_snapshot reads it, so the page's text after a colon never names one."""
    return _line_refs(snapshot, BUTTON_LINE)


def entry_refs(snapshot: str) -> list[str]:
    """The refs of the snapshot's text entries (textbox, searchbox, spinbutton, combobox), read
    as `button_refs` reads a button's."""
    return _line_refs(snapshot, ENTRY_LINE)


async def _names(page: Any, ref: str) -> list[str] | None:
    """The names in the native file input at ref, None for any other element, and [] for a ref
    that no longer resolves or an answer that is not a short list of short names. One call,
    which never waits for the element: a ref gone since the snapshot raises at once."""
    try:
        # Not evaluate_all: it resolves refs in the main world, which lacks snapshotForAI's cache.
        names = await page.eval_on_selector(f"aria-ref={ref}", NATIVE_FILES_JS)
    except Exception:
        return []
    if names is None:
        return None
    if not isinstance(names, list) or len(names) > NAMES_MAX:
        return []
    return names if all(isinstance(n, str) and len(n) <= NAME_MAX for n in names) else []


async def native_files(page: Any, snapshot: str) -> dict[str, list[str]]:
    """Current native FileLists at this snapshot's button refs. Empty selections are explicit,
    so a stale filename echoed by the page cannot hide a cleared or replaced input; a ref this
    read cannot hold (gone since the snapshot, past NATIVE_REFS_MAX, or past the names' budget
    of SNAPSHOT_MAX characters) reads [] too, as holding no file, and never fails the snapshot.
    Chooser buttons are not native inputs: no entry, they keep their visible readback path."""
    from browserd.tools.policy import SNAPSHOT_MAX

    selected: dict[str, list[str]] = {}
    size = 2
    for index, ref in enumerate(button_refs(snapshot)):
        names = await _names(page, ref) if index < NATIVE_REFS_MAX else []
        if names is None:
            continue
        cost = len(json.dumps({ref: names}, ensure_ascii=True)) if names else 0
        if size + cost > SNAPSHOT_MAX:
            names, cost = [], 0
        selected[ref], size = names, size + cost
    return selected


async def credentials(page: Any, snapshot: str) -> dict[str, bool]:
    """Whether each text entry of this snapshot is a credential input (detect.CREDENTIAL_FN), by
    its ref. A ref this read cannot hold (gone since the snapshot, past CREDENTIAL_REFS_MAX, a
    read that raises or answers no boolean) is left out: the app reads it as unknown, and falls
    back on its label. Never fails the snapshot."""
    found: dict[str, bool] = {}
    for ref in entry_refs(snapshot)[:CREDENTIAL_REFS_MAX]:
        if (kind := await _credential(page, ref)) is not None:
            found[ref] = kind
    return found


async def _credential(page: Any, ref: str) -> bool | None:
    try:
        kind = await page.eval_on_selector(f"aria-ref={ref}", CREDENTIAL_JS)
    except Exception:
        return None
    return kind if isinstance(kind, bool) else None


async def front(page: Any) -> Any:
    await page.bring_to_front()
    return page


async def navigate(
    page: Any,
    url: str,
    timeout_s: float = NAVIGATION_S,
    *,
    before_input: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Navigates the tab (brought to the front first) and waits for `load`, at most timeout_s.
    Past it, or on a network error, the page is stopped and a failure result is returned."""
    from playwright.async_api import Error
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    if before_input is not None:
        before_input()
    await front(page)
    if before_input is not None:
        before_input()
    try:
        response = await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
    except PlaywrightTimeout:
        with contextlib.suppress(Error):
            await page.evaluate("window.stop()")
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
