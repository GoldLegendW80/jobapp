"""Task 3 evidence: /api/health and /docs through the compose dev stack, screenshots here.

Run from app/: uv run --native-tls --with playwright python tasks/evidence/task3/check.py tasks/evidence/task3
"""

import json
import sys

from playwright.sync_api import sync_playwright

out = sys.argv[1]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    resp = page.goto("http://localhost:8000/api/health")
    assert resp.status == 200, resp.status
    assert json.loads(page.inner_text("body")) == {"status": "ok"}
    page.screenshot(path=f"{out}/health.png")
    resp = page.goto("http://localhost:8000/docs")
    assert resp.status == 200
    page.wait_for_selector(".opblock-summary-path", timeout=15000)
    title = page.locator(".info .title").first.inner_text()
    page.locator(".opblock-summary-path").first.click()
    page.locator("button:has-text('Try it out')").click()
    page.locator("button:has-text('Execute')").click()
    page.wait_for_selector(
        ".live-responses-table tbody .response-col_status", timeout=10000
    )
    status = page.locator(
        ".live-responses-table tbody .response-col_status"
    ).first.inner_text()
    assert status.strip() == "200", status
    page.screenshot(path=f"{out}/docs.png", full_page=True)
    print(
        "health ok; docs title:",
        title.replace("\n", " "),
        "; try it out status",
        status,
    )
    browser.close()
