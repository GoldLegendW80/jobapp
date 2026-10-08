import json
import sys

from playwright.sync_api import sync_playwright

base, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 400})
    response = page.goto(f"{base}/api/health")
    assert response is not None and response.status == 503, response and response.status
    body = json.loads(page.inner_text("body"))
    assert body == {"status": "error", "detail": "database unavailable"}, body
    page.screenshot(path=f"{out}/health-db-down-503.png")
    print("db down:", response.status, body)
    browser.close()
