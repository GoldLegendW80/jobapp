"""Jobs J22 on real Chromium in the browser container: the reader's two scripts
(linkedin_page.CARDS_SCRIPT, POSTING_SCRIPT) run by browser_evaluate on a sweep lease's anonymous
discovery (browser B20), over the invented LinkedIn search and posting pages of
tests/browser_pages/j22/ (served by tests/browser_pages/server.py). No test reaches LinkedIn.
Needs an existing BROWSER_TEST_IMAGE; never builds or pulls one.
"""

import json
import os
import uuid

import pytest

from app.browser.sources import linkedin_page as page
from tests.browser.test_b5_container import Lease
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
PAGES = "http://pages:8080/j22/linkedin/jobs"


@pytest.fixture(scope="module")
def j22(tmp_path_factory):
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path_factory.mktemp("j22") / "j22.yml"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "browser": {
                        "pull_policy": "never",
                        "volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"],
                        "environment": {"BROWSER_PROXY_ALLOW_PRIVATE": "pages"},
                    },
                    "client": {"pull_policy": "never"},
                    "pages": {"pull_policy": "never"},
                }
            }
        )
    )
    stack = Stack("j22-page-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        yield stack
    finally:
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


def test_the_scripts_read_the_cards_and_the_posting_on_real_chromium(j22):
    lease = Lease(j22, "sweep", hosts=["pages"])
    j22.redactions.append(lease.token)
    # The scope the reader sends: its start alone, never `signed_in` (a fresh context).
    marker = {"_discovery": "native_get", "_discovery_scope": {"start": f"{PAGES}/search"}}
    try:
        opened = lease.tool("browser_navigate", url=f"{PAGES}/search/engineer.html", **marker)
        assert opened["ok"], opened
        read = lease.tool("browser_evaluate", function=page.CARDS_SCRIPT, **marker)
        assert read["ok"], read
        cards = page.cards_of(page._loads(read))
        assert [(c.job_id, c.title, c.company, c.location, c.published) for c in cards] == [
            ("4200000101", "AI Engineer", "Example Reunion", "Saint-Denis, Réunion, Reunion",
             "2026-11-12"),
            ("4200000102", "Data Engineer", "Example Ocean", "Saint-Pierre, Réunion, Reunion",
             "2026-11-17"),
            ("4200000103", "ML Engineer", "Example Miami", "Miami, FL", "2026-11-10"),
        ]  # fmt: skip
        opened = lease.tool("browser_navigate", url=f"{PAGES}/view/4200000101/", **marker)
        assert opened["ok"], opened
        read = lease.tool("browser_evaluate", function=page.POSTING_SCRIPT, **marker)
        assert read["ok"], read
        detail = page.posting_of(page._loads(read))
        assert detail is not None
        assert "builds agents for its island customers" in detail.description
        assert detail.criteria == {
            "Seniority level": "Mid-Senior level",
            "Employment type": "Full-time",
        }
        assert (detail.title, detail.company, detail.location) == (
            "AI Engineer",
            "Example Reunion",
            "Saint-Denis, Réunion, Reunion",
        )
    finally:
        lease.end()
