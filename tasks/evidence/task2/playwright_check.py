import json
import sys

from playwright.sync_api import expect, sync_playwright

base, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    response = page.goto(f"{base}/api/health")
    assert response is not None and response.status == 200, response and response.status
    body = json.loads(page.inner_text("body"))
    assert body == {"status": "ok"}, body
    page.screenshot(path=f"{out}/health-ok.png")
    print("health:", response.status, body)

    response = page.goto(f"{base}/docs")
    assert response is not None and response.status == 200
    expect(page.locator(".info .title").first).to_contain_text(
        "jobapp api", timeout=15000
    )
    page.get_by_role("button", name="GET /api/health Health").click()
    page.get_by_role("button", name="Try it out").click()
    page.get_by_role("button", name="Execute").click()
    live = page.locator(".live-responses-table")
    expect(live.locator("tbody .response-col_status").first).to_contain_text("200")
    expect(live.locator("tbody .response-col_description").first).to_contain_text(
        '"status": "ok"'
    )
    page.screenshot(path=f"{out}/docs-try-health.png", full_page=True)
    print("docs: title ok, Try it out returned 200")
    browser.close()
