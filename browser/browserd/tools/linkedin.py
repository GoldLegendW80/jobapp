"""Code-owned Easy Apply opener. This is deliberately absent from the model's tool table.

LinkedIn Help distinguishes the opener, popup screens, Review and Submit application:
https://www.linkedin.com/help/linkedin/answer/a512388 (checked 2026-10-22).
No selector, ref, label, script or field value is accepted from the caller.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from browserd.tools import actions

PURPOSES = frozenset({"apply_fill", "full_auto", "apply_submit"})
NAMES = re.compile(r"^(?:Easy Apply|Candidature simplifiée)$")
JOB = re.compile(r"[0-9]{1,24}")
# Fresh before and after the human approach. Check the native element and click point, not
# merely its accessible label: a form-associated button, overlay or nested control refuses.
OPENER_JS = """(e, selectors) => {
  if (e.ownerDocument.defaultView !== e.ownerDocument.defaultView.top ||
      e.tagName !== 'BUTTON' || e.type !== 'button' || e.form || e.closest('form') ||
      e.disabled || e.getAttribute('aria-disabled') === 'true' ||
      !['Easy Apply', 'Candidature simplifiée'].includes(
        (e.getAttribute('aria-label') || e.textContent || '').trim()) ||
      e.querySelector('button,input,select,textarea,a[href],[role=button],form') ||
      e.closest('dialog,[role=dialog],[role=alertdialog]')) return false;
  try { if (selectors.some(s => e.matches(s) || e.querySelector(s))) return false; }
  catch (_) { return false; }
  const r = e.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const point = e.ownerDocument.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
  return !!point && point.closest('button') === e;
}"""


def job_matches(url: str, job_id: str, fixture_base: str = "") -> bool:
    if not JOB.fullmatch(job_id):
        return False
    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment:
        return False
    if parts.scheme == "https" and parts.netloc in ("linkedin.com", "www.linkedin.com"):
        return parts.path.rstrip("/") == f"/jobs/view/{job_id}"
    if fixture_base:
        base = urlsplit(fixture_base)
        return (parts.scheme, parts.netloc) == (base.scheme, base.netloc) and parts.path.rstrip(
            "/"
        ) == base.path.rstrip("/") + f"/jobs/view/{job_id}"
    return False


async def open_modal(tab: Any, job_id: str, fixture_base: str = "") -> dict[str, Any]:
    if tab.request.purpose not in PURPOSES or not tab.request.automation:
        return actions.refused("linkedin_opener_refused")
    page = await tab.page()

    async def valid(button: Any) -> bool:
        return (
            job_matches(page.url, job_id, fixture_base)
            and page.main_frame.url == page.url
            and await button.count() == 1
            and await button.evaluate(OPENER_JS, list(tab.request.submit_selectors)) is True
        )

    button = page.get_by_role("button", name=NAMES, exact=True)
    if not await valid(button):
        return actions.refused("linkedin_opener_refused")
    await tab.approach(button)
    if not await valid(button):
        return actions.refused("linkedin_opener_refused")
    await button.click(timeout=actions.ACTION_S * 1000)
    return await tab.settle({"ok": True})
